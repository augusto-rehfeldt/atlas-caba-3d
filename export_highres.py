"""Render seamless browser tiles and merge them into one high-resolution PNG."""

from __future__ import annotations

import argparse
import http.server
import math
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlencode

from PIL import Image


ROOT = Path(__file__).parent


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass


def find_browser() -> str:
    candidates = [
        shutil.which("chrome"),
        shutil.which("msedge"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise SystemExit("Chrome or Edge was not found")


def main(width: int, height: int, tile_size: int, output: Path, night: bool) -> None:
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0),
        lambda *args, **kwargs: QuietHandler(*args, directory=str(ROOT), **kwargs),
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    browser = find_browser()
    columns, rows = math.ceil(width / tile_size), math.ceil(height / tile_size)
    merged = Image.new("RGB", (width, height))

    try:
        with tempfile.TemporaryDirectory(prefix=".render-", dir=ROOT) as temporary:
            temporary = Path(temporary)
            total = columns * rows
            count = 0
            for row in range(rows):
                for column in range(columns):
                    count += 1
                    x, y = column * tile_size, row * tile_size
                    tile_width = min(tile_size, width - x)
                    tile_height = min(tile_size, height - y)
                    tile = temporary / f"tile-{row}-{column}.png"
                    profile = temporary / f"profile-{row}-{column}"
                    query = urlencode({"export": 1, "width": width, "height": height, "x": x, "y": y, "night": int(night)})
                    print(f"Rendering tile {count}/{total}…")
                    result = subprocess.run(
                        [
                            browser,
                            "--headless=new",
                            "--hide-scrollbars",
                            "--disable-extensions",
                            "--run-all-compositor-stages-before-draw",
                            "--virtual-time-budget=15000",
                            f"--user-data-dir={profile}",
                            f"--window-size={tile_width},{tile_height}",
                            f"--screenshot={tile}",
                            f"http://127.0.0.1:{server.server_port}/index.html?{query}",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    if result.returncode or not tile.exists():
                        raise SystemExit(result.stderr[-1500:] or "Browser render failed")
                    with Image.open(tile) as rendered:
                        merged.paste(rendered.convert("RGB").crop((0, 0, tile_width, tile_height)), (x, y))
        output.parent.mkdir(parents=True, exist_ok=True)
        merged.save(output, optimize=True)
        print(f"Wrote {output} ({width} × {height})")
    finally:
        server.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=6144)
    parser.add_argument("--height", type=int, default=3456)
    parser.add_argument("--tile", type=int, default=2048)
    parser.add_argument("--output", type=Path, default=ROOT / "flores-highres.png")
    parser.add_argument("--night", action="store_true", help="render with illuminated windows")
    args = parser.parse_args()
    if min(args.width, args.height, args.tile) < 256:
        parser.error("width, height, and tile must be at least 256 pixels")
    main(args.width, args.height, args.tile, args.output, args.night)
