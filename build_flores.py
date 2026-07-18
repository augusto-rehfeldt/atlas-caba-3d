"""Build the small browser dataset from official Buenos Aires open data."""

from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

import shapefile


ROOT = Path(__file__).parent
CACHE = ROOT / ".cache"
DATA = ROOT / "data" / "flores.json"
DATA_JS = ROOT / "data" / "flores-data.js"
SOURCES = {
    "boundary": "https://cdn.buenosaires.gob.ar/datosabiertos/datasets/innovacion-transformacion-digital/barrios/barrios.geojson",
    "buildings": "https://cdn.buenosaires.gob.ar/datosabiertos/datasets/secretaria-de-desarrollo-urbano/tejido-urbano/tejido.zip",
    "roads": "https://cdn.buenosaires.gob.ar/datosabiertos/datasets/jefatura-de-gabinete-de-ministros/calles/callejero.geojson",
    "parks": "https://cdn.buenosaires.gob.ar/datosabiertos/datasets/secretaria-de-desarrollo-urbano/espacios-verdes/espacio_verde_publico.geojson",
}


def normalized(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(c for c in text if not unicodedata.combining(c)).upper()


def download(name: str, refresh: bool) -> Path:
    suffix = ".zip" if name == "buildings" else ".geojson"
    target = CACHE / f"{name}{suffix}"
    if target.exists() and not refresh:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {name}…", flush=True)
    request = urllib.request.Request(SOURCES[name], headers={"User-Agent": "FloresMap/1.0"})
    with urllib.request.urlopen(request) as response, target.open("wb") as output:
        downloaded = 0
        next_report = 64 * 1024 * 1024
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            downloaded += len(chunk)
            if downloaded >= next_report:
                print(f"  {downloaded / 1024 / 1024:.0f} MB…", flush=True)
                next_report += 64 * 1024 * 1024
    return target


def rings(geometry: dict | None) -> list[list[list[float]]]:
    if not geometry:
        return []
    coordinates = geometry.get("coordinates", [])
    if geometry.get("type") == "Polygon":
        return coordinates[:1]
    if geometry.get("type") == "MultiPolygon":
        return [polygon[0] for polygon in coordinates if polygon]
    return []


def lines(geometry: dict | None) -> list[list[list[float]]]:
    if not geometry:
        return []
    coordinates = geometry.get("coordinates", [])
    if geometry.get("type") == "LineString":
        return [coordinates]
    if geometry.get("type") == "MultiLineString":
        return coordinates
    return []


def bounds(polygons: list[list[list[float]]]) -> tuple[float, float, float, float]:
    points = [point for polygon in polygons for point in polygon]
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def inside(point: tuple[float, float], polygon: list[list[float]]) -> bool:
    x, y = point
    hit = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            hit = not hit
        j = i
    return hit


def localizer(center: tuple[float, float]):
    lon0, lat0 = center
    lon_scale = 111_320 * math.cos(math.radians(lat0))

    def local(points: list[list[float]]) -> list[list[float]]:
        result: list[list[float]] = []
        for lon, lat, *_ in points:
            point = [round((lon - lon0) * lon_scale, 1), round((lat - lat0) * 110_540, 1)]
            if not result or point != result[-1]:
                result.append(point)
        if len(result) > 1 and result[0] == result[-1]:
            result.pop()
        return result

    return local


def number(value: object) -> float | None:
    if value is None:
        return None
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(value))
    return float(match.group().replace(",", ".")) if match else None


def building_height(record: dict) -> float:
    for field in ("altura", "EXTR_2017", "alt_2017", "alt_2013", "alt_ant"):
        value = number(record.get(field))
        if value and 2 <= value <= 180:
            return round(value, 1)
    floors = number(record.get("altos")) or number(record.get("piso_2017")) or 1
    return round(min(max(floors * 3, 3), 90), 1)


def extract_buildings(path: Path, local, boundary_geo: list[list[list[float]]]) -> list[dict]:
    directory = CACHE / "tejido"
    if not (directory / "tejido.shp").exists():
        print("Extracting buildings…", flush=True)
        with zipfile.ZipFile(path) as archive:
            archive.extractall(CACHE)
    reader = shapefile.Reader(str(directory / "tejido.shp"), encoding="latin1")
    fields = [field[0] for field in reader.fields[1:]]
    west, south, east, north = bounds(boundary_geo)
    buildings: list[dict] = []
    for shape_record in reader.iterShapeRecords():
        shape = shape_record.shape
        shape_west, shape_south, shape_east, shape_north = shape.bbox
        center = ((shape_west + shape_east) / 2, (shape_south + shape_north) / 2)
        if not (west <= center[0] <= east and south <= center[1] <= north):
            continue
        if not any(inside(center, polygon) for polygon in boundary_geo):
            continue
        record = dict(zip(fields, shape_record.record))
        points = shape.points
        parts = list(shape.parts) + [len(points)]
        for start, end in zip(parts, parts[1:]):
            polygon = local(points[start:end])
            if len(polygon) >= 3:
                buildings.append({
                    "p": polygon,
                    "h": building_height(record),
                    "id": str(record.get("id") or record.get("objectid") or record.get("gid") or len(buildings)),
                })
    return buildings


def road_width(properties: dict) -> int:
    name = normalized(properties.get("nom_mapa") or properties.get("nomoficial"))
    kind = normalized(properties.get("tipo_c"))
    hierarchy = normalized(properties.get("red_jerarq"))
    if "AV" in kind and any(road in name for road in ("RIVADAVIA", "DIRECTORIO", "NAZCA", "SAN PEDRITO", "ALBERDI", "EVA PERON", "VARELA", "CARABOBO")):
        return 18
    if "AV" in kind or "PRINCIPAL" in hierarchy:
        return 13
    if "PASAJE" in kind:
        return 5
    return 8


def main(refresh: bool = False) -> None:
    boundary_data = json.loads(download("boundary", refresh).read_text(encoding="utf-8-sig"))
    boundary_feature = next(
        feature for feature in boundary_data["features"]
        if normalized(feature["properties"].get("nombre")) == "FLORES"
    )
    boundary_geo = rings(boundary_feature["geometry"])
    west, south, east, north = bounds(boundary_geo)
    center = ((west + east) / 2, (south + north) / 2)
    local = localizer(center)

    buildings = extract_buildings(download("buildings", refresh), local, boundary_geo)

    roads_data = json.loads(download("roads", refresh).read_text(encoding="utf-8-sig"))
    roads = []
    for feature in roads_data["features"]:
        properties = feature.get("properties", {})
        neighborhoods = " ".join(str(properties.get(key) or "") for key in ("barrio", "barrio_par", "barrio_imp"))
        if "FLORES" not in normalized(neighborhoods):
            continue
        for line in lines(feature.get("geometry")):
            path = local(line)
            if len(path) >= 2:
                roads.append({
                    "p": path,
                    "n": str(properties.get("nom_mapa") or properties.get("nomoficial") or ""),
                    "w": road_width(properties),
                    "rail": "FFCC" in normalized(properties.get("tipo_ffcc")) or "FERROCARRIL" in normalized(properties.get("tipo_ffcc")),
                })

    parks_data = json.loads(download("parks", refresh).read_text(encoding="utf-8-sig"))
    parks = []
    for feature in parks_data["features"]:
        properties = feature.get("properties", {})
        if "FLORES" not in normalized(properties.get("barrio")):
            continue
        for polygon in rings(feature.get("geometry")):
            path = local(polygon)
            if len(path) >= 3:
                parks.append({"p": path, "n": str(properties.get("nom_mapa") or properties.get("nombre_ev") or properties.get("nombre") or "")})

    boundary = [local(polygon) for polygon in boundary_geo]
    landmarks = [
        {"n": "Plaza Flores", "p": local([[-58.46365, -34.62838]])[0], "k": "park"},
        {"n": "Basílica San José", "p": local([[-58.46305, -34.62915]])[0], "k": "landmark"},
        {"n": "Estación Flores", "p": local([[-58.4658, -34.6277]])[0], "k": "station"},
    ]
    payload = {
        "meta": {
            "name": "Flores",
            "center": [round(center[0], 6), round(center[1], 6)],
            "bbox": [west, south, east, north],
            "counts": {"buildings": len(buildings), "roads": len(roads), "parks": len(parks)},
            "license": "Datos: GCBA Buenos Aires Data, CC BY 2.5 AR / CC BY 4.0",
        },
        "boundary": boundary,
        "buildings": buildings,
        "roads": roads,
        "parks": parks,
        "landmarks": landmarks,
    }
    validate(payload)
    DATA.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    DATA.write_text(serialized, encoding="utf-8")
    DATA_JS.write_text(f"window.FLORES_DATA={serialized};\n", encoding="utf-8")
    print(f"Wrote {DATA} ({DATA.stat().st_size / 1_000_000:.1f} MB): {payload['meta']['counts']}")


def validate(data: dict) -> None:
    counts = data["meta"]["counts"]
    assert data["meta"]["name"] == "Flores"
    assert counts["buildings"] == len(data["buildings"]) and counts["buildings"] > 1_000
    assert counts["roads"] == len(data["roads"]) and counts["roads"] > 100
    assert any("RIVADAVIA" in normalized(road["n"]) for road in data["roads"])
    assert any("PUEYRRED" in normalized(park["n"]) or "FLORES" in normalized(park["n"]) for park in data["parks"])
    assert all(2 <= building["h"] <= 180 and len(building["p"]) >= 3 for building in data["buildings"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="redownload source data")
    parser.add_argument("--check", action="store_true", help="validate the existing browser dataset")
    args = parser.parse_args()
    if args.check:
        validate(json.loads(DATA.read_text(encoding="utf-8")))
        print("Flores data check passed")
    else:
        main(args.refresh)
