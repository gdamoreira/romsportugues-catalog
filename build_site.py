#!/usr/bin/env python3
"""
Build a static catalogue site from roms.csv.

Outputs a ready-to-publish folder:

    site/
      index.html          all cards pre-rendered (works without JS)
      assets/styles.css
      assets/app.js       search, filters, sort, theme toggle
      assets/og-image.jpg 1200x630 social preview card
      assets/icon-*.png   manifest icons
      favicon.svg
      apple-touch-icon.png
      site.webmanifest
      robots.txt
      .nojekyll

The generator only needs the Python standard library.

    python3 build_site.py
    python3 build_site.py --csv roms.csv --out site
    python3 build_site.py --base-url https://example.com/catalogue
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import shutil
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "site_src"

CONSOLE_COLORS = {
    "Playstation 2 PS2": "#4f8ef7",
    "Super Nintendo SNES": "#a78bfa",
    "Game Boy Advance GBA": "#8b5cf6",
    "Playstation 1 PS1": "#60a5fa",
    "Nintendo NES (Famicon)": "#ef4444",
    "Mega Drive": "#22d3ee",
    "PSP": "#38bdf8",
    "Nintendo DS": "#fb7185",
    "Nintendo Wii": "#67e8f9",
    "Game Boy Color GBC": "#fbbf24",
    "Master System": "#f97316",
    "Game Boy": "#84cc16",
    "Nintendo 64 N64": "#f43f5e",
    "Game Cube": "#818cf8",
    "Dreamcast": "#fb923c",
    "Saturn": "#94a3b8",
}
DEFAULT_COLOR = "#7c5cff"


def esc(value: str) -> str:
    return html.escape(value or "", quote=True)


def clean(value: str) -> str:
    """Unescape entities and collapse whitespace."""
    return " ".join(html.unescape(value or "").split())


def fold(value: str) -> str:
    """Accent-insensitive sort key."""
    return (
        unicodedata.normalize("NFKD", value or "")
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )


def split_genres(raw: str) -> list:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def br_number(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def load_rows(csv_path: Path) -> list:
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["_title"] = clean(row.get("dados_nome") or row.get("nome"))
        row["_console"] = clean(row.get("dados_categoria")) or "Sem categoria"
        row["_genres"] = split_genres(clean(row.get("dados_genero")))
        row["_downloads"] = []
        for index in range(1, 30):
            url = clean(row.get(f"download_{index}_url"))
            if not url:
                continue
            label = clean(row.get(f"download_{index}_label")) or f"Servidor {index}"
            row["_downloads"].append((label, url))
    return rows


def build_card(row: dict, index: int) -> str:
    title = row["_title"]
    console = row["_console"]
    color = CONSOLE_COLORS.get(console, DEFAULT_COLOR)
    year = clean(row.get("dados_ano"))
    url = clean(row.get("url"))
    image = clean(row.get("imagem"))
    genres = row["_genres"]

    meta = [
        ("Desenvolvedor", clean(row.get("dados_desenvolvedor"))),
        ("Tradutor", clean(row.get("dados_tradutor"))),
        ("Jogador", clean(row.get("dados_jogador"))),
    ]
    meta_html = "".join(
        f'<div><dt>{esc(label)}</dt><dd>{esc(value)}</dd></div>'
        for label, value in meta
        if value
    )

    genres_html = ""
    if genres:
        chips = "".join(f"<li>{esc(genre)}</li>" for genre in genres)
        genres_html = f'<ul class="rom-genres" aria-label="Gêneros">{chips}</ul>'

    downloads_html = ""
    if row["_downloads"]:
        buttons = "".join(
            f'<a class="dl-btn" href="{esc(download_url)}" target="_blank" '
            f'rel="noopener noreferrer nofollow" title="{esc(label)}">'
            f'<svg class="icon" aria-hidden="true"><use href="#i-download"></use></svg>'
            f"<span>{esc(label)}</span></a>"
            for label, download_url in row["_downloads"]
        )
        downloads_html = f'<div class="rom-downloads">{buttons}</div>'

    source_link = (
        f'<a class="source-link" href="{esc(url)}" target="_blank" '
        f'rel="noopener noreferrer nofollow">Ver página original '
        f'<svg class="icon" aria-hidden="true"><use href="#i-external"></use></svg></a>'
    )
    actions_html = (
        f'<div class="rom-actions">{downloads_html}{source_link}</div>'
        if downloads_html
        else f'<div class="rom-actions">{source_link}</div>'
    )

    search_blob = " ".join(
        [
            title,
            console,
            year,
            clean(row.get("dados_desenvolvedor")),
            clean(row.get("dados_tradutor")),
            clean(row.get("dados_genero")),
            clean(row.get("dados_jogador")),
        ]
    )

    return f"""
<article class="rom-card" data-index="{index}" data-name="{esc(title)}"
         data-console="{esc(console)}" data-genres="{esc('|'.join(genres))}"
         data-year="{esc(year)}" data-search="{esc(search_blob)}"
         style="--console:{color}">
  <a class="rom-cover" href="{esc(url)}" target="_blank" rel="noopener noreferrer nofollow"
     aria-label="Abrir a página de {esc(title)} no site original">
    <img src="{esc(image)}" alt="Capa de {esc(title)}" loading="{'eager' if index < 8 else 'lazy'}"
         decoding="async" width="500" height="451" referrerpolicy="no-referrer">
    <span class="console-badge">{esc(console)}</span>
    {f'<span class="year-badge">{esc(year)}</span>' if year else ''}
  </a>
  <div class="rom-body">
    <h2 class="rom-title"><a href="{esc(url)}" target="_blank"
        rel="noopener noreferrer nofollow" title="{esc(title)}">{esc(title)}</a></h2>
    <dl class="rom-meta">{meta_html}</dl>
    {genres_html}
    {actions_html}
  </div>
</article>""".strip()


def build_options(rows: list) -> tuple:
    console_counts = Counter(row["_console"] for row in rows)
    consoles = sorted(console_counts, key=lambda name: (-console_counts[name], fold(name)))
    console_options = "".join(
        f'<option value="{esc(name)}">{esc(name)} ({br_number(console_counts[name])})</option>'
        for name in consoles
    )

    genre_counts = Counter(genre for row in rows for genre in row["_genres"])
    genres = sorted(genre_counts, key=fold)
    genre_options = "".join(
        f'<option value="{esc(name)}">{esc(name)} ({br_number(genre_counts[name])})</option>'
        for name in genres
    )
    return console_options, genre_options, console_counts, genre_counts


THEME_SCRIPT = """<script>
(function () {
  try {
    var stored = localStorage.getItem('roms-theme');
    if (!stored && window.matchMedia('(prefers-color-scheme: light)').matches) stored = 'light';
    if (stored) document.documentElement.dataset.theme = stored;
  } catch (error) { /* localStorage disabled */ }
})();
</script>"""

SPRITE = """<svg class="svg-sprite" aria-hidden="true" focusable="false">
  <symbol id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.5-3.5"></path></symbol>
  <symbol id="i-download" viewBox="0 0 24 24"><path d="M12 3v12"></path><path d="m8 11 4 4 4-4"></path><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"></path></symbol>
  <symbol id="i-sun" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"></path></symbol>
  <symbol id="i-moon" viewBox="0 0 24 24"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"></path></symbol>
  <symbol id="i-arrow-up" viewBox="0 0 24 24"><path d="M12 19V5"></path><path d="m6 11 6-6 6 6"></path></symbol>
  <symbol id="i-external" viewBox="0 0 24 24"><path d="M14 3h7v7"></path><path d="m21 3-9 9"></path><path d="M10 5H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5"></path></symbol>
  <symbol id="i-x" viewBox="0 0 24 24"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></symbol>
</svg>"""


def build_page(
    rows: list,
    console_options: str,
    genre_options: str,
    console_counts,
    genre_counts,
    base_url: str,
) -> str:
    total = len(rows)
    years = [int(row["dados_ano"]) for row in rows if clean(row.get("dados_ano")).isdigit()]
    year_min, year_max = (min(years), max(years)) if years else ("", "")
    download_total = sum(len(row["_downloads"]) for row in rows)
    console_total = len(console_counts)
    generated = date.today().strftime("%d/%m/%Y")
    cards = "\n".join(build_card(row, index) for index, row in enumerate(rows))

    base_url = (base_url or "").rstrip("/")
    page_url = f"{base_url}/"
    image_url = f"{base_url}/assets/og-image.jpg"

    title = f"ROMs em Português — Catálogo de {br_number(total)} ROMs traduzidas"
    description = (
        f"Catálogo completo com {br_number(total)} ROMs traduzidas para português, "
        f"de {console_total} consoles entre {year_min} e {year_max}. "
        "Veja capas, dados do jogo e links de download."
    )
    image_alt = (
        f"Card do catálogo ROMs em Português com {br_number(total)} ROMs traduzidas "
        f"para {console_total} consoles, de {year_min} a {year_max}."
    )

    schema = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "WebSite",
                "@id": f"{page_url}#website",
                "url": page_url,
                "name": "ROMs em Português",
                "description": description,
                "inLanguage": "pt-BR",
            },
            {
                "@type": "CollectionPage",
                "@id": f"{page_url}#webpage",
                "url": page_url,
                "name": title,
                "description": description,
                "isPartOf": {"@id": f"{page_url}#website"},
                "inLanguage": "pt-BR",
                "primaryImageOfPage": {
                    "@type": "ImageObject",
                    "@id": f"{page_url}#primaryimage",
                    "url": image_url,
                    "contentUrl": image_url,
                    "width": 1200,
                    "height": 630,
                    "caption": "Catálogo ROMs em Português",
                },
                "mainEntity": {
                    "@type": "ItemList",
                    "name": "ROMs traduzidas em português",
                    "numberOfItems": total,
                },
            },
        ],
    }
    json_ld = json.dumps(
        schema, ensure_ascii=False, separators=(",", ":")
    ).replace("</", "<\\/")

    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1">
<link rel="canonical" href="{esc(page_url)}">
<meta name="theme-color" content="#080b10">
<meta name="application-name" content="ROMs em Português">
<meta name="author" content="gdamoreira">

<meta property="og:locale" content="pt_BR">
<meta property="og:site_name" content="ROMs em Português">
<meta property="og:type" content="website">
<meta property="og:url" content="{esc(page_url)}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:image" content="{esc(image_url)}">
<meta property="og:image:secure_url" content="{esc(image_url)}">
<meta property="og:image:type" content="image/jpeg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="{esc(image_alt)}">

<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(description)}">
<meta name="twitter:image" content="{esc(image_url)}">
<meta name="twitter:image:alt" content="{esc(image_alt)}">

<link rel="image_src" href="{esc(image_url)}">
<link rel="icon" href="favicon.svg" type="image/svg+xml">
<link rel="icon" type="image/png" sizes="32x32" href="assets/favicon-32.png">
<link rel="apple-touch-icon" sizes="180x180" href="apple-touch-icon.png">
<link rel="manifest" href="site.webmanifest">
<meta name="msapplication-TileColor" content="#080b10">
<meta name="msapplication-TileImage" content="{esc(base_url)}/assets/mstile-150.png">
<script type="application/ld+json">{json_ld}</script>
{THEME_SCRIPT}
<link rel="stylesheet" href="assets/styles.css">
</head>
<body>
{SPRITE}

<header class="site-header">
  <div class="header-inner">
    <a class="brand" href="./" aria-label="ROMs em Português — início">
      <span class="brand-mark" aria-hidden="true">RP</span>
      <span class="brand-text">
        <span class="brand-name">ROMs em Português</span>
        <span class="brand-tag">Catálogo PT-BR</span>
      </span>
    </a>
    <button class="theme-toggle" id="theme-toggle" type="button" aria-label="Alternar tema">
      <svg class="icon icon-sun" aria-hidden="true"><use href="#i-sun"></use></svg>
      <svg class="icon icon-moon" aria-hidden="true"><use href="#i-moon"></use></svg>
    </button>
  </div>
</header>

<main>
  <section class="hero">
    <p class="eyebrow">Catálogo completo</p>
    <h1>ROMs traduzidas <span class="accent">em português</span></h1>
    <p class="lead">
      Explore {br_number(total)} traduções fan-made para {console_total} consoles,
      de {year_min} a {year_max}. Capas, dados do jogo e links de download em um só lugar.
    </p>
    <div class="hero-stats">
      <div class="stat"><b>{br_number(total)}</b><span>ROMs</span></div>
      <div class="stat"><b>{console_total}</b><span>consoles</span></div>
      <div class="stat"><b>{year_min}–{year_max}</b><span>anos</span></div>
      <div class="stat"><b>{br_number(download_total)}</b><span>links de download</span></div>
    </div>
    <div class="search-box">
      <svg class="icon" aria-hidden="true"><use href="#i-search"></use></svg>
      <label class="sr-only" for="search">Buscar ROMs</label>
      <input id="search" type="search" placeholder="Buscar por jogo, desenvolvedor, tradutor…"
             autocomplete="off" spellcheck="false">
      <button class="search-clear" id="search-clear" type="button" aria-label="Limpar busca" hidden>
        <svg class="icon" aria-hidden="true"><use href="#i-x"></use></svg>
      </button>
    </div>
  </section>

  <div class="toolbar">
    <div class="toolbar-inner">
      <div class="control">
        <label for="console">Console</label>
        <select id="console">
          <option value="">Todos os consoles</option>
          {console_options}
        </select>
      </div>
      <div class="control">
        <label for="genre">Gênero</label>
        <select id="genre">
          <option value="">Todos os gêneros</option>
          {genre_options}
        </select>
      </div>
      <div class="control">
        <label for="sort">Ordenar</label>
        <select id="sort">
          <option value="recent">Adicionados recentemente</option>
          <option value="name">Nome (A–Z)</option>
          <option value="year-desc">Ano (mais novo)</option>
          <option value="year-asc">Ano (mais antigo)</option>
          <option value="console">Console (A–Z)</option>
        </select>
      </div>
      <button class="btn-reset" id="reset" type="button">
        <svg class="icon" aria-hidden="true"><use href="#i-x"></use></svg>
        Limpar
      </button>
      <p class="results" id="results" aria-live="polite">{br_number(total)} de {br_number(total)} ROMs</p>
    </div>
  </div>

  <section class="catalog" aria-label="Lista de ROMs">
    <div class="grid" id="grid">
{cards}
    </div>
    <div class="empty" id="empty">
      <p class="empty-title">Nenhuma ROM encontrada</p>
      <p>Tente outro termo ou limpe os filtros.</p>
      <button class="btn-reset" id="empty-reset" type="button">Limpar filtros</button>
    </div>
  </section>
</main>

<footer class="site-footer">
  <div class="footer-inner">
    <div class="footer-about">
      <p><strong>ROMs em Português</strong> é um catálogo estático gerado a partir de
        <a href="https://romsportugues.com/" target="_blank" rel="noopener noreferrer">romsportugues.com</a>.</p>
      <p>Todo o conteúdo, capas e links pertencem aos seus respectivos autores.
        Esta página apenas indexa informações publicamente disponíveis.</p>
    </div>
    <div class="footer-meta">
      <p>{br_number(total)} ROMs · {br_number(download_total)} links · {generated}</p>
      <p>HTML + CSS estático · sem rastreamento</p>
    </div>
  </div>
</footer>

<button class="to-top" id="to-top" type="button" aria-label="Voltar ao topo">
  <svg class="icon" aria-hidden="true"><use href="#i-arrow-up"></use></svg>
</button>

<script src="assets/app.js" defer></script>
</body>
</html>
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the static ROM catalogue site.")
    parser.add_argument("--csv", default="roms.csv", help="input CSV path")
    parser.add_argument("--out", default="site", help="output directory")
    parser.add_argument("--src", default=str(SRC_DIR), help="source assets directory")
    parser.add_argument(
        "--base-url",
        default="https://damoreira.com.br/romsportugues-catalog",
        help="public URL used for canonical, Open Graph and Twitter tags",
    )
    args = parser.parse_args(argv)

    csv_path = Path(args.csv)
    out_dir = Path(args.out)
    src_dir = Path(args.src)
    if not csv_path.exists():
        parser.error(f"CSV not found: {csv_path}")
    if not (src_dir / "styles.css").exists():
        parser.error(f"asset sources not found in: {src_dir}")

    rows = load_rows(csv_path)
    console_options, genre_options, console_counts, genre_counts = build_options(rows)
    page = build_page(
        rows,
        console_options,
        genre_options,
        console_counts,
        genre_counts,
        args.base_url,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "assets").mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(page, encoding="utf-8")
    for name in (
        "styles.css",
        "app.js",
        "og-image.jpg",
        "favicon-32.png",
        "icon-192.png",
        "icon-512.png",
        "mstile-150.png",
    ):
        shutil.copyfile(src_dir / name, out_dir / "assets" / name)
    for name in (
        "favicon.svg",
        "apple-touch-icon.png",
        "site.webmanifest",
        "robots.txt",
    ):
        shutil.copyfile(src_dir / name, out_dir / name)
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    size = (out_dir / "index.html").stat().st_size
    print(
        f"Built {out_dir}/index.html — {len(rows)} ROMs, "
        f"{len(console_counts)} consoles, {size / 1024:.0f} KB"
    )
    print(f"Assets copied to {out_dir}/assets/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
