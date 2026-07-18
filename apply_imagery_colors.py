"""Sample real roof colours from a licensed, north-up aerial/satellite image."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import urllib.request
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).parent
DATA = ROOT / "data" / "flores.json"
DATA_JS = ROOT / "data" / "flores-data.js"
TILE_URL = "https://servicios.usig.buenosaires.gob.ar/mapcache/tms/1.0.0/fotografias_aereas_2021_caba_3857@GoogleMapsCompatible/{z}/{x}/{y}.png"


def imagery_sample(image: Image.Image, x: int, y: int, radius: int = 1, warmth: float = .16) -> tuple[str, str]:
    pixels = [
        image.getpixel((px, py))
        for px in range(max(0, x - radius), min(image.width, x + radius + 1))
        for py in range(max(0, y - radius), min(image.height, y + radius + 1))
    ]
    observed = [int(statistics.median(pixel[channel] for pixel in pixels)) for channel in range(3)]
    # Keep the observed hue while lifting deep aerial shadows for the illustration.
    channels = [round(value * (1 - warmth) + warm * warmth) for value, warm in zip(observed, (225, 211, 188))]
    red, green, blue = observed
    light = sum(observed) / 3
    spread = max(observed) - min(observed)
    if green > red * 1.08 and green > blue * 1.06:
        material = "green"
    elif red > green * 1.12 and red > blue * 1.18:
        material = "tile"
    elif light < 90:
        material = "dark"
    elif spread < 22 and light > 155:
        material = "concrete"
    elif blue > red * 1.08 or (spread < 18 and light > 115):
        material = "metal"
    else:
        material = "mixed"
    return "#" + "".join(f"{value:02x}" for value in channels), material


def roof_colour(image: Image.Image, x: int, y: int) -> str:
    return imagery_sample(image, x, y)[0]


def position(building: dict, center: list[float]) -> tuple[float, float]:
    lon0, lat0 = center
    lon_scale = 111_320 * math.cos(math.radians(lat0))
    x = sum(point[0] for point in building["p"]) / len(building["p"])
    y = sum(point[1] for point in building["p"]) / len(building["p"])
    return lon0 + x / lon_scale, lat0 + y / 110_540


def write(data: dict) -> None:
    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    DATA.write_text(serialized, encoding="utf-8")
    DATA_JS.write_text(f"window.FLORES_DATA={serialized};\n", encoding="utf-8")


def apply_image(data: dict, image_path: Path, bbox: list[float] | None) -> int:
    west, south, east, north = bbox or data["meta"]["bbox"]
    image = Image.open(image_path).convert("RGB")
    coloured = 0

    for building in data["buildings"]:
        lon, lat = position(building, data["meta"]["center"])
        if not (west <= lon <= east and south <= lat <= north):
            continue
        px = round((lon - west) / (east - west) * (image.width - 1))
        py = round((north - lat) / (north - south) * (image.height - 1))
        building["c"], building["m"] = imagery_sample(image, px, py)
        coloured += 1

    for road in data["roads"]:
        lon, lat = position(road, data["meta"]["center"])
        if west <= lon <= east and south <= lat <= north:
            px = round((lon - west) / (east - west) * (image.width - 1))
            py = round((north - lat) / (north - south) * (image.height - 1))
            road["c"] = imagery_sample(image, px, py, radius=2, warmth=.05)[0]

    data["meta"]["imagery"] = {"source": image_path.name, "method": "building-centroid sample"}
    return coloured


def apply_gcba(data: dict, zoom: int) -> int:
    cache = ROOT / ".cache" / "aerial-2021" / str(zoom)
    cache.mkdir(parents=True, exist_ok=True)
    tiles: dict[tuple[int, int], Image.Image] = {}
    downloads = 0

    def sample(lon: float, lat: float, radius: int = 1, warmth: float = .16) -> tuple[str, str]:
        nonlocal downloads
        size = 2 ** zoom
        tile_x = (lon + 180) / 360 * size
        tile_y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * size
        x, y = math.floor(tile_x), math.floor(tile_y)
        key = (x, y)
        if key not in tiles:
            target = cache / f"{x}-{y}.png"
            if not target.exists():
                request = urllib.request.Request(
                    TILE_URL.format(z=zoom, x=x, y=size - 1 - y),
                    headers={"User-Agent": "AtlasCABA3D/1.0"},
                )
                with urllib.request.urlopen(request, timeout=30) as response, target.open("wb") as output:
                    output.write(response.read())
                downloads += 1
                if downloads % 25 == 0:
                    print(f"Downloaded {downloads} aerial tiles…", flush=True)
            tiles[key] = Image.open(target).convert("RGB")
        px = min(255, max(0, math.floor((tile_x - x) * 256)))
        py = min(255, max(0, math.floor((tile_y - y) * 256)))
        return imagery_sample(tiles[key], px, py, radius=radius, warmth=warmth)

    for building in data["buildings"]:
        building["c"], building["m"] = sample(*position(building, data["meta"]["center"]))
    for road in data["roads"]:
        road["c"] = sample(*position(road, data["meta"]["center"]), radius=2, warmth=.05)[0]

    data["meta"]["imagery"] = {
        "source": "GCBA IDECABA — Fotografía aérea 2021",
        "license": "CC BY 2.5 AR",
        "url": "https://data.buenosaires.gob.ar/dataset/tiles-fotografia-aerea",
        "zoom": zoom,
        "method": "building-centroid sample",
    }
    print(f"Used {len(tiles)} official aerial tiles ({downloads} downloaded)")
    return len(data["buildings"])


def main(image_path: Path | None, bbox: list[float] | None, gcba: bool, zoom: int) -> None:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    coloured = apply_gcba(data, zoom) if gcba else apply_image(data, image_path, bbox)
    write(data)
    print(f"Applied imagery-derived colours to {coloured:,} building volumes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", nargs="?", type=Path, help="north-up image covering the supplied bounding box")
    parser.add_argument("--gcba-aerial", action="store_true", help="sample the official GCBA 2021 aerial tiles")
    parser.add_argument("--zoom", type=int, choices=range(15, 19), default=17, help="GCBA tile zoom (default: 17)")
    parser.add_argument(
        "--bbox",
        type=lambda value: [float(part) for part in value.split(",")],
        help="image bounds as west,south,east,north; defaults to the Flores data bounds",
    )
    args = parser.parse_args()
    if args.bbox and len(args.bbox) != 4:
        parser.error("--bbox needs four comma-separated numbers")
    if bool(args.image) == args.gcba_aerial:
        parser.error("provide either an image or --gcba-aerial")
    main(args.image, args.bbox, args.gcba_aerial, args.zoom)
