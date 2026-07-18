"""Pre-render every barrio volume into the saved full-CABA map."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).parent
DATA = ROOT / "data"
BACKGROUND = "#172428"
MATERIALS = {
    "tile": "#a56f5c", "concrete": "#aaa79f", "metal": "#87969a",
    "green": "#7b826d", "dark": "#716b65", "mixed": "#927d70",
}


def read_script(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8").split("=", 1)[1][:-2])


def rgb(colour: str) -> tuple[int, int, int]:
    return tuple(int(colour[index:index + 2], 16) for index in (1, 3, 5))


def shade(colour: str, amount: float) -> tuple[int, int, int]:
    return tuple(round(channel * amount) for channel in rgb(colour))


def mix(colour: str, target: str, amount: float) -> str:
    channels = [round(a * (1 - amount) + b * amount) for a, b in zip(rgb(colour), rgb(target))]
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def street_colour(colour: str) -> tuple[int, int, int]:
    observed = rgb(colour)
    light = min(154, max(54, observed[0] * .21 + observed[1] * .72 + observed[2] * .07))
    return tuple(round((light + offset) * .72 + channel * .28) for channel, offset in zip(observed, (6, 1, -7)))


def aerial_ground(meta: dict, boundary: list, iso, min_x: float, min_y: float, width: int, height: int, scale: float) -> Image.Image:
    """Warp the highest fully cached GCBA aerial layer onto the isometric ground plane."""
    west, south, east, north = meta["bbox"]
    selected = None
    for zoom in (17, 16, 15):
        directory = ROOT / ".cache" / "aerial-2021" / str(zoom)
        size = 2 ** zoom

        def tile(lon: float, lat: float) -> tuple[float, float]:
            return (
                (lon + 180) / 360 * size,
                (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * size,
            )

        left, top = tile(west, north)
        right, bottom = tile(east, south)
        x0, x1 = math.floor(left), math.floor(right)
        y0, y1 = math.floor(top), math.floor(bottom)
        paths = [(x, y, directory / f"{x}-{y}.png") for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
        if paths and all(path.exists() for _, _, path in paths):
            selected = zoom, size, x0, y0, x1, y1, paths
            break

    background = Image.new("RGB", (width, height), BACKGROUND)
    mask = Image.new("L", (width, height))
    mask_draw = ImageDraw.Draw(mask)

    def pixel(point: tuple[float, float]) -> tuple[int, int]:
        return round((point[0] - min_x) * scale), round((point[1] - min_y) * scale)

    for polygon in boundary:
        mask_draw.polygon([pixel(iso(point)) for point in polygon], fill=255)
    if not selected:
        ground = Image.new("RGB", (width, height), "#cfc3a6")
        return Image.composite(ground, background, mask)

    _, size, x0, y0, x1, y1, paths = selected
    mosaic = Image.new("RGB", ((x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256))
    for x, y, path in paths:
        with Image.open(path) as tile_image:
            mosaic.paste(tile_image.convert("RGB"), ((x - x0) * 256, (y - y0) * 256))

    lon0, lat0 = meta["center"]
    lon_scale = 111_320 * math.cos(math.radians(lat0))

    def source(u: float, v: float) -> tuple[float, float]:
        x, y = u / scale + min_x, v / scale + min_y
        local_east = (x / .62 + y / .31) / 2
        local_north = (x / .62 - y / .31) / 2
        lon = lon0 + local_east / lon_scale
        lat = lat0 + local_north / 110_540
        tile_x = (lon + 180) / 360 * size
        tile_y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * size
        return (tile_x - x0) * 256, (tile_y - y0) * 256

    origin, horizontal, vertical = source(0, 0), source(1, 0), source(0, 1)
    transform = (
        horizontal[0] - origin[0], vertical[0] - origin[0], origin[0],
        horizontal[1] - origin[1], vertical[1] - origin[1], origin[1],
    )
    texture = mosaic.transform(
        (width, height), Image.Transform.AFFINE, transform,
        resample=Image.Resampling.BICUBIC, fillcolor=BACKGROUND,
    )
    return Image.composite(texture, background, mask)


def draw_building(draw: ImageDraw.ImageDraw, polygon: list[tuple[float, float]], building: dict, pixel, scale: float) -> None:
    """Draw one satellite-coloured extrusion; add façade detail only where pixels can carry it."""
    roof = building.get("c", "#d8c3a1")
    facade = mix(roof, MATERIALS.get(building.get("m"), "#978274"), .58)
    height = building["h"]
    base = [pixel(point) for point in polygon]
    top = [pixel(point, height) for point in polygon]
    detailed = scale >= 1 and height >= 30
    draw.polygon(base, fill=shade(facade, .55))
    for side, start in enumerate(base):
        end = base[(side + 1) % len(base)]
        draw.polygon(
            [start, end, top[(side + 1) % len(top)], top[side]],
            fill=shade(facade, .8 if end[0] > start[0] else .63),
        )
        edge = math.dist(polygon[side], polygon[(side + 1) % len(polygon)])
        if not detailed or edge < 8:
            continue
        columns = min(4, max(1, round(edge / 9)))
        floors = min(6, max(2, round(height / 6)))
        for floor in range(floors):
            z0 = 2 + floor * (height - 4) / floors
            z1 = min(height - 1, z0 + 1.8)
            for column in range(columns):
                u0 = (column + .2) / columns
                u1 = (column + .78) / columns
                a, b = polygon[side], polygon[(side + 1) % len(polygon)]
                low_start = (a[0] + (b[0] - a[0]) * u0, a[1] + (b[1] - a[1]) * u0)
                low_end = (a[0] + (b[0] - a[0]) * u1, a[1] + (b[1] - a[1]) * u1)
                draw.polygon(
                    [pixel(low_start, z0), pixel(low_end, z0), pixel(low_end, z1), pixel(low_start, z1)],
                    fill="#38545a",
                )
    draw.polygon(top, fill=roof)
    draw.line(top + top[:1], fill=shade(roof, .7), width=max(1, round(scale * .7)), joint="curve")
    texture = int(building.get("t", 0))
    roof_width = max(point[0] for point in top) - min(point[0] for point in top)
    roof_height = max(point[1] for point in top) - min(point[1] for point in top)
    if scale >= 1 and texture and max(roof_width, roof_height) >= 6:
        centre_x = round(sum(point[0] for point in top) / len(top))
        centre_y = round(sum(point[1] for point in top) / len(top))
        contrast, roughness = texture & 3, texture >> 2
        seed = sum(ord(char) for char in building["id"])
        colour = mix(roof, "#263438", .08 + contrast * .035)
        for mark in range(1 + roughness):
            dx = (seed * (mark + 3) % 7 - 3) * max(1, round(scale))
            dy = (seed * (mark + 5) % 5 - 2) * max(1, round(scale))
            draw.point((centre_x + dx, centre_y + dy), fill=colour)


def main(scale: float, quality: int) -> None:
    index = read_script(DATA / "barrios-index.js")[1:]
    caba = read_script(DATA / "caba-data.js")
    caba_lon, caba_lat = caba["meta"]["center"]
    caba_lon_scale = 111_320 * math.cos(math.radians(caba_lat))

    def iso(point: list[float]) -> tuple[float, float]:
        east, north = point
        return (east + north) * .62, (east - north) * .31

    boundary = [iso(point) for polygon in caba["boundary"] for point in polygon]
    min_x = min(point[0] for point in boundary) - 30
    max_x = max(point[0] for point in boundary) + 30
    min_y = min(point[1] for point in boundary) - 220
    max_y = max(point[1] for point in boundary) + 30
    width = math.ceil((max_x - min_x) * scale)
    height = math.ceil((max_y - min_y) * scale)
    image = aerial_ground(caba["meta"], caba["boundary"], iso, min_x, min_y, width, height, scale)

    def pixel(point: tuple[float, float], z: float = 0) -> tuple[int, int]:
        return round((point[0] - min_x) * scale), round((point[1] - z * 1.12 - min_y) * scale)

    areas: list[tuple[Path, tuple[float, float], float]] = []
    road_count = park_count = expected = 0
    overlay = Image.new("RGBA", image.size)
    draw = ImageDraw.Draw(overlay)
    for entry in index:
        path = ROOT / entry["file"]
        data = read_script(path)
        lon0, lat0 = data["meta"]["center"]
        lon_scale = 111_320 * math.cos(math.radians(lat0))

        def project(point: list[float]) -> tuple[float, float]:
            lon = lon0 + point[0] / lon_scale
            lat = lat0 + point[1] / 110_540
            return iso([(lon - caba_lon) * caba_lon_scale, (lat - caba_lat) * 110_540])

        centre_depth = project([0, 0])[1]
        areas.append((path, (lon0, lat0), centre_depth))
        expected += data["meta"]["counts"]["buildings"]
        for park in data["parks"]:
            if len(park["p"]) < 3:
                continue
            draw.polygon([pixel(project(point)) for point in park["p"]], fill=(115, 143, 104, 72))
            park_count += 1
        for road in data["roads"]:
            if len(road["p"]) < 2:
                continue
            colour = (90, 87, 82) if road.get("rail") else street_colour(road.get("c", "#807b70"))
            draw.line(
                [pixel(project(point)) for point in road["p"]],
                fill=(*colour, 105),
                width=max(1, round(road.get("w", 8) * scale)),
                joint="curve",
            )
            road_count += 1

    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(image)

    building_count = 0
    for path, (lon0, lat0), _ in sorted(areas, key=lambda item: item[2]):
        data = read_script(path)
        lon_scale = 111_320 * math.cos(math.radians(lat0))

        def project(point: list[float]) -> tuple[float, float]:
            lon = lon0 + point[0] / lon_scale
            lat = lat0 + point[1] / 110_540
            return iso([(lon - caba_lon) * caba_lon_scale, (lat - caba_lat) * 110_540])

        buildings = []
        for building in data["buildings"]:
            polygon = [project(point) for point in building["p"]]
            buildings.append((max(point[1] for point in polygon), polygon, building))
        for _, polygon, building in sorted(buildings, key=lambda item: item[0]):
            draw_building(draw, polygon, building, pixel, scale)
        building_count += len(buildings)
        print(f"Rendered {data['meta']['name']}: {building_count:,} volumes", flush=True)

    assert building_count == expected, (building_count, expected)
    output = DATA / "caba-render.webp"
    image.save(output, "WEBP", quality=quality, method=4)
    manifest = {
        "src": "data/caba-render.webp", "scale": scale,
        "x": min_x, "y": min_y, "width": width, "height": height,
        "buildings": building_count, "roads": road_count, "parks": park_count,
    }
    (DATA / "caba-render.js").write_text(
        "window.CABA_RENDER=" + json.dumps(manifest, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    shell = {**caba, "meta": {**caba["meta"], "counts": {**caba["meta"]["counts"], "buildings": 0}}, "buildings": []}
    (DATA / "caba-data.js").write_text(
        "window.MAP_DATA=" + json.dumps(shell, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    print(f"Saved {output.name}: {width} × {height}, {building_count:,} buildings", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", type=float, default=.6)
    parser.add_argument("--quality", type=int, default=88)
    args = parser.parse_args()
    if not .1 <= args.scale <= 1 or not 50 <= args.quality <= 100:
        parser.error("scale must be .1–1 and quality 50–100")
    main(args.scale, args.quality)
