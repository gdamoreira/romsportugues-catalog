#!/usr/bin/env python3
"""
Generate the 1200x630 Open Graph card from the current roms.csv.

The card is rendered with headless Chrome so the typography and layout match
the site. Chrome (or Chromium/Edge/Brave) must be installed; everything else
is Python standard library.

    python3 make_og_image.py
    python3 make_og_image.py --csv roms.csv --out site_src/og-image.png
    CHROME_PATH=/path/to/chrome python3 make_og_image.py

The script downloads four cover images from romsportugues.com, inlines them
as data URIs, and falls back to branded placeholders if a cover is unavailable.
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import mimetypes
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = BASE_DIR / "site_src" / "og-image.png"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Covers that make a recognisable, console-diverse card. Any missing title is
# replaced with the next ROM that has a cover image.
PREFERRED_COVERS = [
    "Devil May Cry 1",
    "Sonic The Hedgehog",
    "The World Ends with You",
    "Super Bomberman",
]

TEMPLATE = r"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { width: 1200px; height: 630px; overflow: hidden; }
  body {
    font-family: Arial, "Liberation Sans", "Helvetica Neue", sans-serif;
    background:
      radial-gradient(720px 520px at 6% -12%, rgba(124, 92, 255, .34), transparent 62%),
      radial-gradient(700px 520px at 102% 8%, rgba(34, 211, 238, .18), transparent 60%),
      radial-gradient(560px 460px at 72% 116%, rgba(124, 92, 255, .16), transparent 62%),
      #080b10;
    color: #e8eef6;
    position: relative;
  }
  body::before {
    content: "";
    position: absolute;
    inset: 0;
    background-image:
      linear-gradient(rgba(255, 255, 255, .045) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255, 255, 255, .045) 1px, transparent 1px);
    background-size: 46px 46px;
    -webkit-mask-image: radial-gradient(ellipse at 28% 42%, #000 0%, transparent 74%);
    mask-image: radial-gradient(ellipse at 28% 42%, #000 0%, transparent 74%);
    opacity: .55;
  }
  .frame {
    position: absolute;
    inset: 0;
    padding: 56px 64px;
    display: flex;
    justify-content: space-between;
    z-index: 2;
  }
  .left { width: 636px; display: flex; flex-direction: column; }
  .brand { display: flex; align-items: center; gap: 14px; }
  .brand-mark {
    width: 54px; height: 54px; border-radius: 15px;
    background: linear-gradient(135deg, #7c5cff 0%, #22d3ee 100%);
    display: grid; place-items: center;
    font-size: 17px; font-weight: 850; letter-spacing: .04em; color: #fff;
    box-shadow: 0 14px 34px -10px rgba(124, 92, 255, .85);
  }
  .brand-name { font-size: 21px; font-weight: 800; letter-spacing: -.02em; line-height: 1.15; }
  .brand-tag {
    margin-top: 3px; font-size: 10px; font-weight: 700; letter-spacing: .22em;
    text-transform: uppercase; color: #6b7a90;
  }
  .kicker {
    margin-top: 44px; font-size: 11px; font-weight: 800; letter-spacing: .24em;
    text-transform: uppercase; color: #22d3ee;
  }
  h1 {
    margin-top: 12px; font-size: 64px; line-height: 1.03;
    letter-spacing: -.045em; font-weight: 850;
  }
  h1 .grad {
    background: linear-gradient(90deg, #8f74ff 0%, #22d3ee 100%);
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }
  .lead {
    margin-top: 18px; max-width: 548px;
    font-size: 20px; line-height: 1.45; color: #93a1b5;
  }
  .stats { margin-top: auto; display: flex; padding-top: 18px; }
  .stat { padding: 0 26px; border-left: 1px solid rgba(255, 255, 255, .12); }
  .stat:first-child { padding-left: 0; border-left: 0; }
  .stat b {
    display: block; font-size: 28px; line-height: 1.1;
    letter-spacing: -.02em; font-variant-numeric: tabular-nums;
  }
  .stat span {
    display: block; margin-top: 5px; font-size: 10px; font-weight: 700;
    letter-spacing: .14em; text-transform: uppercase; color: #6b7a90;
  }
  .domain {
    margin-top: 22px; font-size: 12px; letter-spacing: .02em; color: #6b7a90;
  }
  .domain b { color: #93a1b5; font-weight: 600; }
  .right {
    position: relative; width: 430px;
    display: flex; align-items: center; justify-content: center;
  }
  .glow {
    position: absolute; width: 400px; height: 400px; border-radius: 50%;
    background: radial-gradient(circle, rgba(124, 92, 255, .38) 0%, transparent 66%);
    filter: blur(8px);
  }
  .collage {
    position: relative; display: grid; grid-template-columns: 190px 190px;
    grid-auto-rows: 230px; gap: 12px; transform: rotate(-4deg);
  }
  .cover {
    width: 190px; height: 230px; border-radius: 14px; overflow: hidden;
    border: 1px solid rgba(255, 255, 255, .16);
    background: #0d1219;
    box-shadow: 0 26px 54px -18px rgba(0, 0, 0, .95);
  }
  .cover img { width: 100%; height: 100%; object-fit: cover; object-position: center top; display: block; }
  .cover.placeholder {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 6px; padding: 14px; text-align: center;
    background: linear-gradient(160deg, rgba(124, 92, 255, .40), rgba(34, 211, 238, .18)), #0d1219;
  }
  .cover.placeholder b { font-size: 14px; line-height: 1.3; overflow-wrap: anywhere; }
  .cover.placeholder small {
    font-size: 9px; font-weight: 700; letter-spacing: .12em;
    text-transform: uppercase; color: #93a1b5;
  }
  .badge {
    position: absolute; left: -26px; bottom: -20px;
    display: flex; align-items: center; gap: 9px;
    padding: 10px 16px; border-radius: 999px;
    background: rgba(8, 11, 16, .9);
    border: 1px solid rgba(255, 255, 255, .16);
    box-shadow: 0 12px 30px rgba(0, 0, 0, .65);
  }
  .badge::before {
    content: ""; width: 8px; height: 8px; border-radius: 50%;
    background: #22d3ee; box-shadow: 0 0 12px #22d3ee;
  }
  .badge b { display: block; font-size: 12px; font-weight: 700; letter-spacing: .03em; }
  .badge small {
    display: block; margin-top: 2px; font-size: 9px; font-weight: 700;
    letter-spacing: .14em; text-transform: uppercase; color: #6b7a90;
  }
</style>
</head>
<body>
  <div class="frame">
    <div class="left">
      <div class="brand">
        <div class="brand-mark">RP</div>
        <div>
          <div class="brand-name">ROMs em Português</div>
          <div class="brand-tag">Catálogo PT-BR</div>
        </div>
      </div>

      <div class="kicker">Catálogo completo · PT-BR</div>
      <h1>ROMs traduzidas<br><span class="grad">em português</span></h1>
      <p class="lead">{{LEAD}}</p>

      <div class="stats">
        <div class="stat"><b>{{TOTAL}}</b><span>ROMs</span></div>
        <div class="stat"><b>{{CONSOLES}}</b><span>consoles</span></div>
        <div class="stat"><b>{{DOWNLOADS}}</b><span>links</span></div>
      </div>

      <div class="domain"><b>{{DOMAIN}}</b> · dados: romsportugues.com · catálogo não oficial</div>
    </div>

    <div class="right">
      <div class="glow"></div>
      <div class="collage">
        {{COVERS}}
        <div class="badge"><div><b>{{CONSOLES}} consoles</b><small>{{YEARS}}</small></div></div>
      </div>
    </div>
  </div>
</body>
</html>
"""


def clean(value: str) -> str:
    return " ".join(html.unescape(value or "").split())


def fold(value: str) -> str:
    return (
        unicodedata.normalize("NFKD", value or "")
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )


def br_number(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def compute_stats(rows: list) -> dict:
    consoles = {
        clean(row.get("dados_categoria"))
        for row in rows
        if clean(row.get("dados_categoria"))
    }
    years = [
        int(clean(row.get("dados_ano")))
        for row in rows
        if clean(row.get("dados_ano")).isdigit()
    ]
    downloads = 0
    for row in rows:
        for index in range(1, 30):
            if clean(row.get(f"download_{index}_url")):
                downloads += 1

    total = len(rows)
    year_min = min(years) if years else ""
    year_max = max(years) if years else ""
    return {
        "total": total,
        "consoles": len(consoles),
        "downloads": downloads,
        "year_min": year_min,
        "year_max": year_max,
    }


def pick_covers(rows: list) -> list:
    picked = []
    used_urls = set()

    def add(row: dict) -> bool:
        url = clean(row.get("imagem"))
        if not url or url in used_urls:
            return False
        used_urls.add(url)
        picked.append(row)
        return True

    for preferred in PREFERRED_COVERS:
        wanted = fold(preferred)
        for row in rows:
            title = fold(clean(row.get("dados_nome") or row.get("nome")))
            if title.startswith(wanted) and add(row):
                break

    for row in rows:
        if len(picked) >= 4:
            break
        add(row)
    return picked[:4]


def fetch_cover(url: str, timeout: float) -> str | None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Referer": "https://romsportugues.com/"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(8 * 1024 * 1024)
            content_type = response.headers.get_content_type()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        print(f"  cover skipped ({error}): {url}", file=sys.stderr)
        return None

    if not content_type or not content_type.startswith("image/"):
        content_type = mimetypes.guess_type(url)[0] or "image/webp"
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def build_cover_html(rows: list, timeout: float) -> str:
    blocks = []
    for row in rows:
        title = clean(row.get("dados_nome") or row.get("nome")) or "ROM"
        console = clean(row.get("dados_categoria")) or "Catálogo"
        image = fetch_cover(clean(row.get("imagem")), timeout)
        if image:
            blocks.append(f'<div class="cover"><img src="{image}" alt=""></div>')
        else:
            blocks.append(
                f'<div class="cover placeholder"><b>{html.escape(title)}</b>'
                f"<small>{html.escape(console)}</small></div>"
            )
    while len(blocks) < 4:
        blocks.append(
            '<div class="cover placeholder"><b>ROMs</b><small>PT-BR</small></div>'
        )
    return "\n        ".join(blocks)


def render_html(stats: dict, covers_html: str, domain: str) -> str:
    year_min, year_max = stats["year_min"], stats["year_max"]
    if year_min and year_max:
        lead = (
            f"{br_number(stats['total'])} jogos para {stats['consoles']} consoles, "
            f"de {year_min} a {year_max}. Capas, dados do jogo e links de download "
            "em um só lugar."
        )
        years = f"{year_min} – {year_max}"
    else:
        lead = (
            f"{br_number(stats['total'])} jogos para {stats['consoles']} consoles. "
            "Capas, dados do jogo e links de download em um só lugar."
        )
        years = "Catálogo completo"

    replacements = {
        "{{LEAD}}": html.escape(lead),
        "{{TOTAL}}": br_number(stats["total"]),
        "{{CONSOLES}}": str(stats["consoles"]),
        "{{DOWNLOADS}}": br_number(stats["downloads"]),
        "{{YEARS}}": html.escape(years),
        "{{COVERS}}": covers_html,
        "{{DOMAIN}}": html.escape(domain),
    }
    page = TEMPLATE
    for placeholder, value in replacements.items():
        page = page.replace(placeholder, value)
    return page


def find_chrome(explicit: str | None = None) -> str | None:
    candidates = [
        explicit,
        os.environ.get("CHROME_PATH"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "msedge",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
        found = shutil.which(candidate)
        if found:
            return found

    for root in (
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("LOCALAPPDATA"),
    ):
        if not root:
            continue
        for suffix in (
            "Google/Chrome/Application/chrome.exe",
            "Microsoft/Edge/Application/msedge.exe",
            "Chromium/Application/chrome.exe",
        ):
            path = os.path.join(root, suffix)
            if os.path.isfile(path):
                return path
    return None


def png_size(path: Path) -> tuple:
    with open(path, "rb") as handle:
        header = handle.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    return struct.unpack(">II", header[16:24])


def screenshot(chrome: str, html_path: Path, out_path: Path, timeout: float) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="og-image-") as temp:
        profile = Path(temp) / "profile"
        command = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            "--allow-file-access-from-files",
            "--force-device-scale-factor=1",
            "--window-size=1200,630",
            "--virtual-time-budget=10000",
            "--run-all-compositor-stages-before-draw",
            f"--user-data-dir={profile}",
            f"--screenshot={out_path.resolve()}",
            html_path.resolve().as_uri(),
        ]
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Chrome exited with {result.returncode}:\n{message[-2000:]}")
    if not out_path.exists():
        raise RuntimeError(f"Chrome did not write {out_path}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate the 1200x630 Open Graph card from roms.csv.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--csv", default="roms.csv", help="input CSV path")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output PNG path")
    parser.add_argument("--chrome", default=None, help="path to Chrome/Chromium")
    parser.add_argument(
        "--domain",
        default="damoreira.com.br/romsportugues-catalog",
        help="domain shown at the bottom of the card",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="cover download timeout (seconds)"
    )
    parser.add_argument(
        "--html-out",
        default=None,
        help="optional path to save the rendered HTML (debugging)",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    csv_path = Path(args.csv)
    out_path = Path(args.out)
    if not csv_path.exists():
        print(f"error: CSV not found: {csv_path}", file=sys.stderr)
        return 2

    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        print("error: CSV has no rows", file=sys.stderr)
        return 2

    stats = compute_stats(rows)
    print(
        f"Data: {br_number(stats['total'])} ROMs · {stats['consoles']} consoles · "
        f"{stats['year_min']}–{stats['year_max']} · "
        f"{br_number(stats['downloads'])} links"
    )

    selected = pick_covers(rows)
    covers_html = build_cover_html(selected, args.timeout)
    page = render_html(stats, covers_html, args.domain)

    chrome = find_chrome(args.chrome)
    if not chrome:
        print(
            "error: Chrome/Chromium not found. Install Google Chrome or set "
            "CHROME_PATH to the browser binary.",
            file=sys.stderr,
        )
        return 2

    temp_html_dir = None
    if args.html_out:
        html_path = Path(args.html_out)
        html_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        temp_html_dir = tempfile.TemporaryDirectory(prefix="og-image-html-")
        html_path = Path(temp_html_dir.name) / "og.html"
    html_path.write_text(page, encoding="utf-8")

    try:
        screenshot(chrome, html_path, out_path, timeout=max(30.0, args.timeout * 3))
        size = png_size(out_path)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    finally:
        if temp_html_dir is not None:
            temp_html_dir.cleanup()

    if size != (1200, 630):
        print(f"error: unexpected image size {size}, expected (1200, 630)", file=sys.stderr)
        return 1

    print(f"Wrote {out_path} — {size[0]}x{size[1]}, {out_path.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
