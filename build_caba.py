"""Build lazy-loadable isometric datasets for all 48 CABA neighbourhoods."""

from __future__ import annotations

import argparse
import json
import math
import unicodedata
import urllib.request
import zipfile
import zlib
from collections import OrderedDict
from pathlib import Path

import shapefile
from PIL import Image

from apply_imagery_colors import TILE_URL, imagery_sample
from build_flores import (
    CACHE,
    ROOT,
    bounds,
    building_height,
    download,
    inside,
    lines,
    localizer,
    normalized,
    rings,
    road_width,
)


OUTPUT = ROOT / "data" / "barrios"


def slug(value: str) -> str:
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return "-".join(plain.replace("'", "").split())


def locate(point: tuple[float, float], areas: list[dict]) -> dict | None:
    x, y = point
    for area in areas:
        west, south, east, north = area["bbox"]
        if west <= x <= east and south <= y <= north and any(inside(point, polygon) for polygon in area["geo"]):
            return area
    return None


class AerialSampler:
    def __init__(self, zoom: int):
        self.zoom = zoom
        self.size = 2 ** zoom
        self.directory = CACHE / "aerial-2021" / str(zoom)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.tiles: OrderedDict[tuple[int, int], Image.Image] = OrderedDict()
        self.downloads = 0

    def point(self, lon: float, lat: float) -> tuple[Image.Image, int, int]:
        tile_x = (lon + 180) / 360 * self.size
        tile_y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * self.size
        x, y = math.floor(tile_x), math.floor(tile_y)
        key = (x, y)
        if key not in self.tiles:
            target = self.directory / f"{x}-{y}.png"
            if not target.exists():
                request = urllib.request.Request(
                    TILE_URL.format(z=self.zoom, x=x, y=self.size - 1 - y),
                    headers={"User-Agent": "AtlasCABA3D/1.0"},
                )
                with urllib.request.urlopen(request, timeout=30) as response, target.open("wb") as output:
                    output.write(response.read())
                self.downloads += 1
                if self.downloads % 25 == 0:
                    print(f"Downloaded {self.downloads} aerial tiles…", flush=True)
            self.tiles[key] = Image.open(target).convert("RGB")
            if len(self.tiles) > 128:
                self.tiles.popitem(last=False)[1].close()
        else:
            self.tiles.move_to_end(key)
        px = min(255, max(0, math.floor((tile_x - x) * 256)))
        py = min(255, max(0, math.floor((tile_y - y) * 256)))
        return self.tiles[key], px, py

    def __call__(self, lon: float, lat: float, radius: int = 1, warmth: float = .16) -> tuple[str, str]:
        image, px, py = self.point(lon, lat)
        return imagery_sample(image, px, py, radius=radius, warmth=warmth)

    def texture(self, lon: float, lat: float) -> int:
        """Encode real local contrast and roughness in four bits for cheap roof texturing."""
        image, px, py = self.point(lon, lat)
        samples = [image.getpixel((min(255, max(0, px + dx)), min(255, max(0, py + dy)))) for dx, dy in ((0, 0), (-3, 0), (3, 0), (0, -3), (0, 3))]
        luminance = [red * .21 + green * .72 + blue * .07 for red, green, blue in samples]
        contrast = min(3, round((max(luminance) - min(luminance)) / 45))
        roughness = min(3, round(sum(abs(value - luminance[0]) for value in luminance[1:]) / 100))
        return contrast | roughness << 2


def payload(name: str, center: tuple[float, float], bbox: tuple, boundary: list, buildings: list, roads: list, parks: list) -> dict:
    return {
        "meta": {
            "name": name,
            "center": [round(center[0], 6), round(center[1], 6)],
            "bbox": bbox,
            "counts": {"buildings": len(buildings), "roads": len(roads), "parks": len(parks)},
            "license": "Geometría y fotografía aérea 2021: GCBA, CC BY 2.5 AR / CC BY 4.0",
            "imagery": {
                "source": "GCBA IDECABA — Fotografía aérea 2021",
                "license": "CC BY 2.5 AR",
                "url": "https://data.buenosaires.gob.ar/dataset/tiles-fotografia-aerea",
                "buildingTexture": "t: bits 0-1 contrast, bits 2-3 roughness",
            },
        },
        "boundary": boundary,
        "buildings": buildings,
        "roads": roads,
        "parks": parks,
        "landmarks": [],
    }


def write_script(path: Path, data: dict) -> None:
    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"window.MAP_DATA={serialized};\n", encoding="utf-8")


def main(refresh: bool, colours: bool, aerial_zoom: int) -> None:
    boundary_data = json.loads(download("boundary", refresh).read_text(encoding="utf-8-sig"))
    areas = []
    for feature in boundary_data["features"]:
        name = str(feature["properties"].get("nombre") or "").title()
        polygons = rings(feature["geometry"])
        area_bbox = bounds(polygons)
        center = ((area_bbox[0] + area_bbox[2]) / 2, (area_bbox[1] + area_bbox[3]) / 2)
        areas.append({
            "name": name,
            "slug": slug(name),
            "geo": polygons,
            "bbox": area_bbox,
            "center": center,
            "local": localizer(center),
            "buildings": [],
            "roads": [],
            "parks": [],
        })
    areas.sort(key=lambda area: area["name"])
    all_bbox = bounds([polygon for area in areas for polygon in area["geo"]])
    caba_center = ((all_bbox[0] + all_bbox[2]) / 2, (all_bbox[1] + all_bbox[3]) / 2)
    caba_local = localizer(caba_center)
    overview_buildings: list[dict] = []
    overview_roads: list[dict] = []
    overview_parks: list[dict] = []
    sampler = AerialSampler(aerial_zoom) if colours else None

    archive_path = download("buildings", refresh)
    directory = CACHE / "tejido"
    if not (directory / "tejido.shp").exists():
        print("Extracting buildings…", flush=True)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(CACHE)
    reader = shapefile.Reader(str(directory / "tejido.shp"), encoding="latin1")
    fields = [field[0] for field in reader.fields[1:]]
    print(f"Partitioning {len(reader):,} building records across {len(areas)} neighbourhoods…", flush=True)
    for record_number, shape_record in enumerate(reader.iterShapeRecords(), 1):
        shape = shape_record.shape
        west, south, east, north = shape.bbox
        area = locate(((west + east) / 2, (south + north) / 2), areas)
        if not area:
            continue
        record = dict(zip(fields, shape_record.record))
        height = building_height(record)
        identifier = str(record.get("id") or record.get("objectid") or record.get("gid") or record_number)
        points = shape.points
        parts = list(shape.parts) + [len(points)]
        for start, end in zip(parts, parts[1:]):
            geographic = points[start:end]
            polygon = area["local"](geographic)
            if len(polygon) < 3:
                continue
            item = {"p": polygon, "h": height, "id": f"{identifier}-{start}"}
            if sampler:
                lon = sum(point[0] for point in geographic) / len(geographic)
                lat = sum(point[1] for point in geographic) / len(geographic)
                item["c"], item["m"] = sampler(lon, lat)
                item["t"] = sampler.texture(lon, lat)
            area["buildings"].append(item)
            signature = zlib.crc32(item["id"].encode())
            if height >= 36 or signature % 64 == 0:
                overview_polygon = caba_local(geographic)
                if len(overview_polygon) >= 3:
                    overview_buildings.append({**item, "p": overview_polygon})
        if record_number % 100_000 == 0:
            count = sum(len(area["buildings"]) for area in areas)
            print(f"Processed {record_number:,} records / {count:,} volumes…", flush=True)

    roads_data = json.loads(download("roads", refresh).read_text(encoding="utf-8-sig"))
    for feature in roads_data["features"]:
        properties = feature.get("properties", {})
        for geographic in lines(feature.get("geometry")):
            if len(geographic) < 2:
                continue
            area = locate(tuple(geographic[len(geographic) // 2][:2]), areas)
            if not area:
                continue
            width = road_width(properties)
            rail = "FFCC" in normalized(properties.get("tipo_ffcc")) or "FERROCARRIL" in normalized(properties.get("tipo_ffcc"))
            item = {
                "p": area["local"](geographic),
                "n": str(properties.get("nom_mapa") or properties.get("nomoficial") or ""),
                "w": width,
                "rail": rail,
            }
            if sampler:
                lon, lat = geographic[len(geographic) // 2][:2]
                item["c"] = sampler(lon, lat, radius=2, warmth=.05)[0]
            if len(item["p"]) >= 2:
                area["roads"].append(item)
                if width >= 18 or rail:
                    overview_roads.append({**item, "p": caba_local(geographic)})

    parks_data = json.loads(download("parks", refresh).read_text(encoding="utf-8-sig"))
    for feature in parks_data["features"]:
        properties = feature.get("properties", {})
        for geographic in rings(feature.get("geometry")):
            if len(geographic) < 3:
                continue
            lon = sum(point[0] for point in geographic) / len(geographic)
            lat = sum(point[1] for point in geographic) / len(geographic)
            area = locate((lon, lat), areas)
            if not area:
                continue
            name = str(properties.get("nom_mapa") or properties.get("nombre_ev") or properties.get("nombre") or "")
            area["parks"].append({"p": area["local"](geographic), "n": name})
            overview_polygon = caba_local(geographic)
            xs = [point[0] for point in overview_polygon]
            ys = [point[1] for point in overview_polygon]
            if max(max(xs) - min(xs), max(ys) - min(ys)) >= 60:
                overview_parks.append({"p": overview_polygon, "n": name})

    entries = [{"name": "CABA completa", "file": "data/caba-data.js"}]
    for area in areas:
        entries.append({
            "name": area["name"],
            "file": "data/flores-data.js" if normalized(area["name"]) == "FLORES" else f"data/barrios/{area['slug']}.js",
        })
        if normalized(area["name"]) == "FLORES":
            continue
        data = payload(
            area["name"], area["center"], area["bbox"],
            [area["local"](polygon) for polygon in area["geo"]],
            area["buildings"], area["roads"], area["parks"],
        )
        write_script(OUTPUT / f"{area['slug']}.js", data)
        print(f"Wrote {area['name']}: {data['meta']['counts']}", flush=True)

    caba = payload(
        "CABA", caba_center, all_bbox,
        [caba_local(polygon) for area in areas for polygon in area["geo"]],
        overview_buildings, overview_roads, overview_parks,
    )
    write_script(ROOT / "data" / "caba-data.js", caba)
    (ROOT / "data" / "barrios-index.js").write_text(
        "window.CABA_AREAS=" + json.dumps(entries, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    print(f"Wrote CABA overview: {caba['meta']['counts']}", flush=True)
    print(f"Aerial tiles downloaded this run: {sampler.downloads if sampler else 0}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--no-colours", action="store_true", help="skip aerial colour sampling")
    parser.add_argument("--aerial-zoom", type=int, choices=range(15, 19), default=16)
    args = parser.parse_args()
    main(args.refresh, not args.no_colours, args.aerial_zoom)
