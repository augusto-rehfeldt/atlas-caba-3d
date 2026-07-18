"""Attach compact satellite-derived roof texture data to every CABA building."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from apply_imagery_colors import position
from build_caba import AerialSampler


ROOT = Path(__file__).parent


def read_script(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8").split("=", 1)[1][:-2])


def main() -> None:
    entries = read_script(ROOT / "data" / "barrios-index.js")[1:]
    samplers = {16: AerialSampler(16), 17: AerialSampler(17)}
    total = 0
    distribution: Counter[int] = Counter()
    for entry in entries:
        path = ROOT / entry["file"]
        data = read_script(path)
        sampler = samplers[17 if entry["name"] == "Flores" else 16]
        data["meta"]["imagery"]["buildingTexture"] = "t: bits 0-1 contrast, bits 2-3 roughness"
        for building in data["buildings"]:
            building["t"] = sampler.texture(*position(building, data["meta"]["center"]))
            distribution[building["t"]] += 1
        prefix = "window.FLORES_DATA=" if entry["name"] == "Flores" else "window.MAP_DATA="
        serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        path.write_text(prefix + serialized + ";\n", encoding="utf-8")
        if entry["name"] == "Flores":
            (ROOT / "data" / "flores.json").write_text(serialized, encoding="utf-8")
        total += len(data["buildings"])
        print(f"Textured {entry['name']}: {len(data['buildings']):,}", flush=True)
    assert total == 1_388_219 and sum(distribution.values()) == total
    print(f"Applied aerial texture data to {total:,} buildings: {dict(sorted(distribution.items()))}")


if __name__ == "__main__":
    main()
