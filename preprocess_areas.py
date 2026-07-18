"""Save a high-resolution full render and lightweight runtime shell for every barrio."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw

from preprocess_caba import aerial_ground, draw_building, read_script, street_colour


ROOT = Path(__file__).parent
DATA = ROOT / "data"


def render(data: dict, output: Path, scale: float, quality: int) -> dict:
    iso = lambda point: ((point[0] + point[1]) * .62, (point[0] - point[1]) * .31)
    boundary = [iso(point) for polygon in data["boundary"] for point in polygon]
    max_height = max((building["h"] for building in data["buildings"]), default=0)
    min_x = min(point[0] for point in boundary) - 20
    max_x = max(point[0] for point in boundary) + 20
    min_y = min(point[1] for point in boundary) - max_height * 1.12 - 10
    max_y = max(point[1] for point in boundary) + 20
    width = math.ceil((max_x - min_x) * scale)
    height = math.ceil((max_y - min_y) * scale)
    image = aerial_ground(data["meta"], data["boundary"], iso, min_x, min_y, width, height, scale)
    ground_output = DATA / "ground" / output.name
    ground_output.parent.mkdir(parents=True, exist_ok=True)
    image.save(ground_output, "WEBP", quality=max(70, quality - 8), method=4)

    def pixel(point: tuple[float, float], z: float = 0) -> tuple[int, int]:
        return round((point[0] - min_x) * scale), round((point[1] - z * 1.12 - min_y) * scale)

    overlay = Image.new("RGBA", image.size)
    draw = ImageDraw.Draw(overlay)
    for park in data["parks"]:
        if len(park["p"]) >= 3:
            draw.polygon([pixel(iso(point)) for point in park["p"]], fill=(115, 143, 104, 72))
    for road in data["roads"]:
        if len(road["p"]) < 2:
            continue
        colour = (90, 87, 82) if road.get("rail") else street_colour(road.get("c", "#807b70"))
        draw.line(
            [pixel(iso(point)) for point in road["p"]], fill=(*colour, 105),
            width=max(1, round(road.get("w", 8) * scale)), joint="curve",
        )

    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(image)

    buildings = []
    for building in data["buildings"]:
        polygon = [iso(point) for point in building["p"]]
        buildings.append((max(point[1] for point in polygon), polygon, building))
    for _, polygon, building in sorted(buildings, key=lambda item: item[0]):
        draw_building(draw, polygon, building, pixel, scale)

    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, "WEBP", quality=quality, method=4)
    return {
        "src": output.relative_to(ROOT).as_posix(), "scale": scale,
        "ground": ground_output.relative_to(ROOT).as_posix(),
        "x": min_x, "y": min_y, "width": width, "height": height,
        **data["meta"]["counts"],
    }


def main(scale: float, quality: int) -> None:
    entries = read_script(DATA / "barrios-index.js")[1:]
    manifest = {}
    rendered = expected = 0
    for entry in entries:
        source = ROOT / entry["file"]
        data = read_script(source)
        expected += data["meta"]["counts"]["buildings"]
        slug = "flores" if source.name == "flores-data.js" else source.stem
        saved = render(data, DATA / "renders" / f"{slug}.webp", scale, quality)
        shell_path = DATA / "shells" / f"{slug}.js"
        shell_path.parent.mkdir(parents=True, exist_ok=True)
        shell = {**data, "meta": {**data["meta"], "counts": {**data["meta"]["counts"], "buildings": 0}}, "buildings": []}
        shell_path.write_text(
            "window.MAP_DATA=" + json.dumps(shell, ensure_ascii=False, separators=(",", ":")) + ";\n",
            encoding="utf-8",
        )
        saved["shell"] = shell_path.relative_to(ROOT).as_posix()
        manifest[data["meta"]["name"]] = saved
        rendered += saved["buildings"]
        print(f"Saved {data['meta']['name']}: {saved['width']} × {saved['height']}", flush=True)
    assert rendered == expected
    (DATA / "area-renders.js").write_text(
        "window.AREA_RENDERS=" + json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    print(f"Saved {len(manifest)} barrios / {rendered:,} buildings", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", type=float, default=1.2)
    parser.add_argument("--quality", type=int, default=90)
    args = parser.parse_args()
    if not .2 <= args.scale <= 2 or not 50 <= args.quality <= 100:
        parser.error("scale must be .2–2 and quality 50–100")
    main(args.scale, args.quality)
