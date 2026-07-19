"""Cache official GCBA aerial tiles covering every barrio bounding box."""

from __future__ import annotations

import argparse
import math
import urllib.request
from pathlib import Path

from apply_imagery_colors import TILE_URL
from preprocess_caba import read_script


ROOT = Path(__file__).parent


def tiles_for_bbox(bbox: list[float], zoom: int) -> set[tuple[int, int]]:
    west, south, east, north = bbox
    size = 2**zoom

    def tile(lon: float, lat: float) -> tuple[float, float]:
        return (
            (lon + 180) / 360 * size,
            (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * size,
        )

    left, top = tile(west, north)
    right, bottom = tile(east, south)
    return {
        (x, y)
        for y in range(math.floor(top), math.floor(bottom) + 1)
        for x in range(math.floor(left), math.floor(right) + 1)
    }


def main(zoom: int) -> None:
    entries = read_script(ROOT / "data" / "barrios-index.js")[1:]
    required: set[tuple[int, int]] = set()
    for entry in entries:
        required |= tiles_for_bbox(read_script(ROOT / entry["file"])["meta"]["bbox"], zoom)

    cache = ROOT / ".cache" / "aerial-2021" / str(zoom)
    cache.mkdir(parents=True, exist_ok=True)
    missing = [(x, y) for x, y in sorted(required) if not (cache / f"{x}-{y}.png").exists()]
    size = 2**zoom
    for index, (x, y) in enumerate(missing, 1):
        target = cache / f"{x}-{y}.png"
        request = urllib.request.Request(
            TILE_URL.format(z=zoom, x=x, y=size - 1 - y),
            headers={"User-Agent": "AtlasCABA3D/1.0"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            target.write_bytes(response.read())
        if index % 25 == 0 or index == len(missing):
            print(f"Downloaded {index}/{len(missing)} aerial tiles", flush=True)
    print(f"Coverage ready: {len(required)} tiles at zoom {zoom}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zoom", type=int, choices=range(15, 19), default=16)
    main(parser.parse_args().zoom)
