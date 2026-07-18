"""Validate every lazy-loaded barrio dataset without opening a browser."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parent


def read_script(path: Path, prefix: str) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith(prefix) and text.endswith(";\n"), path
    return json.loads(text[len(prefix):-2])


index = read_script(ROOT / "data" / "barrios-index.js", "window.CABA_AREAS=")
assert len(index) == 49
for entry in index:
    path = ROOT / entry["file"]
    prefix = "window.FLORES_DATA=" if path.name == "flores-data.js" else "window.MAP_DATA="
    data = read_script(path, prefix)
    counts = data["meta"]["counts"]
    assert counts["buildings"] == len(data["buildings"]), entry["name"]
    assert counts["roads"] == len(data["roads"]), entry["name"]
    assert counts["parks"] == len(data["parks"]), entry["name"]
    assert data["boundary"] and all(len(building["p"]) >= 3 for building in data["buildings"]), entry["name"]
    assert all(isinstance(building.get("t"), int) and 0 <= building["t"] <= 15 for building in data["buildings"]), entry["name"]
render = read_script(ROOT / "data" / "caba-render.js", "window.CABA_RENDER=")
assert (ROOT / render["src"]).is_file()
assert render["buildings"] == sum(
    read_script(ROOT / entry["file"], "window.FLORES_DATA=" if Path(entry["file"]).name == "flores-data.js" else "window.MAP_DATA=")["meta"]["counts"]["buildings"]
    for entry in index[1:]
)
area_renders = read_script(ROOT / "data" / "area-renders.js", "window.AREA_RENDERS=")
assert len(area_renders) == 48
for entry in index[1:]:
    original = read_script(ROOT / entry["file"], "window.FLORES_DATA=" if Path(entry["file"]).name == "flores-data.js" else "window.MAP_DATA=")
    saved = area_renders[original["meta"]["name"]]
    shell = read_script(ROOT / saved["shell"], "window.MAP_DATA=")
    assert (ROOT / saved["src"]).is_file() and (ROOT / saved["ground"]).is_file()
    assert saved["buildings"] == original["meta"]["counts"]["buildings"]
    assert not shell["buildings"] and shell["boundary"]
print(f"Validated {len(index)} CABA views")
