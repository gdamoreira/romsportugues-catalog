#!/usr/bin/env python3
"""
Scrape romsportugues.com and export every ROM to a CSV file.

Phase 1 (parallel): walks the paginated listing (/page/N/) and collects
                    image, name and URL from every .post-item.
Phase 2 (parallel): opens each ROM page and extracts the "Dados do Jogo"
                    fields and the "Download" links.

Standard library only - no pip installs needed.

Examples
--------
    python3 scrape_roms.py                      # all pages -> roms.csv
    python3 scrape_roms.py --limit 12           # quick smoke test
    python3 scrape_roms.py --start-page 5 --end-page 5
    python3 scrape_roms.py --workers 16 --output data/roms.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser

BASE_URL = "https://romsportugues.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

# Order used for the well-known "Dados do Jogo" fields.
KNOWN_DATA_FIELDS = [
    "Nome", "Categoria", "Desenvolvedor", "Ano", "Tradutor", "Gênero", "Jogador",
]

_print_lock = threading.Lock()


def log(message: str) -> None:
    with _print_lock:
        print(message, file=sys.stderr, flush=True)


def normalize(text: str) -> str:
    """Collapse whitespace and strip."""
    return " ".join((text or "").split())


def slugify(label: str) -> str:
    """'Gênero' -> 'genero', 'Sobre a Tradução' -> 'sobre_a_traducao'."""
    ascii_label = (
        unicodedata.normalize("NFKD", label)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_label).strip("_")
    return slug or "campo"


# ---------------------------------------------------------------------------
# Minimal DOM (stdlib HTMLParser) - needed because the site nests divs deeply
# ---------------------------------------------------------------------------


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list = []
        self.parent: "Node | None" = None

    def get(self, name: str) -> str:
        return self.attrs.get(name) or ""

    def classes(self) -> list:
        return self.get("class").split()

    def iter(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.iter()

    def text_content(self) -> str:
        parts = []
        for child in self.children:
            if isinstance(child, str):
                parts.append(child)
            elif child.tag == "br":
                parts.append("\n")
            elif child.tag in ("script", "style"):
                continue
            else:
                parts.append(child.text_content())
        return "".join(parts)


class DomParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {key: (value or "") for key, value in attrs})
        node.parent = self.stack[-1]
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {key: (value or "") for key, value in attrs})
        node.parent = self.stack[-1]
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_html(html: str) -> Node:
    parser = DomParser()
    parser.feed(html)
    parser.close()
    return parser.root


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class Fetcher:
    """Thread-safe fetcher with a global request rate and retries."""

    def __init__(self, delay: float, timeout: float, retries: int) -> None:
        self.delay = max(0.0, delay)
        self.timeout = timeout
        self.retries = max(0, retries)
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def _wait_turn(self) -> None:
        if self.delay <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next_slot - now)
            self._next_slot = max(now, self._next_slot) + self.delay
        if wait:
            time.sleep(wait)

    def get(self, url: str) -> str:
        last_error = None
        for attempt in range(self.retries + 1):
            self._wait_turn()
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    charset = response.headers.get_content_charset() or "utf-8"
                    return raw.decode(charset, "replace")
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code in (429, 500, 502, 503, 504):
                    retry_after = error.headers.get("Retry-After")
                    pause = (
                        float(retry_after)
                        if retry_after and retry_after.isdigit()
                        else min(30.0, 2.0 ** attempt)
                    )
                    time.sleep(pause)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last_error = error
                if attempt < self.retries:
                    time.sleep(min(30.0, 2.0 ** attempt))
        raise last_error  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Listing pages
# ---------------------------------------------------------------------------


def listing_url(page: int) -> str:
    return BASE_URL + "/" if page <= 1 else f"{BASE_URL}/page/{page}/"


def parse_listing(html: str) -> list:
    items = []
    chunks = html.split('<div class="post-item">')[1:]
    for chunk in chunks:
        chunk = chunk.split("</article>", 1)[0]

        href = ""
        for anchor in re.findall(r"<a\b[^>]*>", chunk):
            if "item-link" in anchor:
                match = re.search(r'href="([^"]+)"', anchor)
                if match:
                    href = match.group(1)
                    break
        if not href:
            match = re.search(
                r'<h3[^>]*class="[^"]*title[^"]*"[^>]*>\s*<a[^>]*href="([^"]+)"',
                chunk,
            )
            if match:
                href = match.group(1)
        if not href:
            continue

        title = ""
        match = re.search(
            r'<h3[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</h3>', chunk, re.S
        )
        if match:
            title = normalize(re.sub(r"<[^>]+>", " ", match.group(1)))

        image = ""
        for attribute in ("src", "data-src", "data-lazy-src"):
            match = re.search(rf'<img\b[^>]*\b{attribute}="([^"]+)"', chunk)
            if match:
                image = match.group(1)
                break

        items.append(
            {
                "imagem": urllib.parse.urljoin(BASE_URL + "/", image) if image else "",
                "nome": title,
                "url": urllib.parse.urljoin(BASE_URL + "/", href),
            }
        )
    return items


def detect_total_pages(html: str) -> int:
    match = re.search(r'data-total="(\d+)"', html)
    if match:
        return int(match.group(1))
    found = [int(number) for number in re.findall(r"/page/(\d+)/", html)]
    return max(found) if found else 1


# ---------------------------------------------------------------------------
# ROM pages
# ---------------------------------------------------------------------------


def find_direct_child(node: Node, class_name: str):
    for child in node.children:
        if isinstance(child, Node) and class_name in child.classes():
            return child
    return None


def find_boxes(root: Node) -> list:
    boxes = []
    for node in root.iter():
        if node.tag == "div" and "su-box" in node.classes():
            title_node = find_direct_child(node, "su-box-title")
            content_node = find_direct_child(node, "su-box-content")
            if title_node is not None and content_node is not None:
                boxes.append((normalize(title_node.text_content()), content_node))
    return boxes


def parse_data_box(content: Node) -> dict:
    data = {}
    for line in content.text_content().splitlines():
        line = normalize(line)
        if ":" not in line:
            continue
        label, value = line.split(":", 1)
        label = normalize(label).rstrip(":").strip()
        value = normalize(value)
        if label and value:
            data[label] = value
    return data


def parse_downloads(content: Node) -> list:
    downloads = []
    for node in content.iter():
        if node.tag != "a" or "su-button" not in node.classes():
            continue
        href = node.get("href")
        if not href:
            continue
        label = normalize(node.text_content())
        label = re.sub(
            r"\s*Clique\s+para\s+fazer\s+download\s*$", "", label, flags=re.I
        )
        label = normalize(label) or f"Download {len(downloads) + 1}"
        downloads.append((label, urllib.parse.urljoin(BASE_URL + "/", href)))
    return downloads


def parse_rom_page(html: str) -> dict:
    root = parse_html(html)
    data, downloads = {}, []
    for title, content in find_boxes(root):
        lowered = title.lower()
        if lowered == "dados do jogo":
            data = parse_data_box(content)
        elif lowered.startswith("download"):
            downloads = parse_downloads(content)
    return {"data": data, "downloads": downloads}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def collect_listing(fetcher, first_html, start_page, end_page, workers, limit):
    """Fetch listing pages in parallel batches, keeping site order.

    When `limit` is set, stop early once enough items were collected.
    """
    page_items = {start_page: parse_listing(first_html)}
    next_page = start_page + 1

    while next_page <= end_page:
        batch_end = min(end_page, next_page + workers - 1)
        pages = range(next_page, batch_end + 1)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fetcher.get, listing_url(p)): p for p in pages}
            for future in as_completed(futures):
                page = futures[future]
                try:
                    page_items[page] = parse_listing(future.result())
                    log(f"  listing page {page} ok")
                except Exception as error:  # noqa: BLE001
                    page_items[page] = []
                    log(f"  listing page {page} FAILED: {error}")
        next_page = batch_end + 1

        if limit and sum(len(items) for items in page_items.values()) >= limit:
            break

    ordered = []
    for page in range(start_page, next_page):
        ordered.extend(page_items.get(page, []))
    return ordered


def fetch_rom_pages(fetcher, rows, workers):
    total = len(rows)
    completed = 0
    started = time.monotonic()

    def work(row):
        html = fetcher.get(row["url"])
        return parse_rom_page(html)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, row): row for row in rows}
        for future in as_completed(futures):
            row = futures[future]
            completed += 1
            try:
                row.update(future.result())
            except Exception as error:  # noqa: BLE001
                row["erro"] = f"{type(error).__name__}: {error}"
            if completed % 25 == 0 or completed == total:
                elapsed = time.monotonic() - started
                rate = completed / elapsed if elapsed else 0
                eta = (total - completed) / rate if rate else 0
                log(
                    f"  details {completed}/{total} "
                    f"({rate:.1f} rom/s, ETA {eta:.0f}s)"
                )
    return rows


def build_columns(rows) -> list:
    seen_fields = []
    for row in rows:
        for field in row.get("data", {}):
            if field not in seen_fields:
                seen_fields.append(field)
    ordered = [field for field in KNOWN_DATA_FIELDS if field in seen_fields]
    ordered += [field for field in seen_fields if field not in KNOWN_DATA_FIELDS]

    max_downloads = max((len(row.get("downloads", [])) for row in rows), default=0)

    columns = ["imagem", "nome", "url"]
    columns += [f"dados_{slugify(field)}" for field in ordered]
    for index in range(1, max_downloads + 1):
        columns.append(f"download_{index}_label")
        columns.append(f"download_{index}_url")
    columns.append("erro")
    return columns, ordered


def write_csv(rows, output_path: str) -> None:
    columns, data_fields = build_columns(rows)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    temp_path = output_path + ".tmp"
    with open(temp_path, "w", newline="", encoding="utf-8-sig") as handle:
        # LF endings keep roms.csv byte-stable across checkouts and refreshes.
        writer = csv.DictWriter(
            handle, fieldnames=columns, restval="", lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            record = {
                "imagem": row.get("imagem", ""),
                "nome": row.get("nome", ""),
                "url": row.get("url", ""),
                "erro": row.get("erro", ""),
            }
            for field in data_fields:
                record[f"dados_{slugify(field)}"] = row.get("data", {}).get(field, "")
            for index, (label, url) in enumerate(row.get("downloads", []), start=1):
                record[f"download_{index}_label"] = label
                record[f"download_{index}_url"] = url
            writer.writerow(record)
    os.replace(temp_path, output_path)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Scrape romsportugues.com into a CSV (image, name, URL, game data, download links).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--output", "-o", default="roms.csv", help="CSV output path")
    parser.add_argument("--workers", "-w", type=int, default=8, help="parallel workers")
    parser.add_argument("--start-page", type=int, default=1, help="first listing page")
    parser.add_argument(
        "--end-page", type=int, default=0, help="last listing page (0 = auto-detect)"
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="max ROMs to scrape (0 = all)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.1,
        help="minimum seconds between any two HTTP requests",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="request timeout")
    parser.add_argument("--retries", type=int, default=3, help="retries per request")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.workers < 1:
        log("error: --workers must be >= 1")
        return 2
    if args.start_page < 1:
        log("error: --start-page must be >= 1")
        return 2

    fetcher = Fetcher(args.delay, args.timeout, args.retries)

    log(f"Fetching listing page {args.start_page} to detect pagination...")
    first_html = fetcher.get(listing_url(args.start_page))
    total_pages = detect_total_pages(first_html)
    end_page = args.end_page or total_pages
    end_page = min(end_page, total_pages)

    log(f"Pages {args.start_page}-{end_page} of {total_pages}.")

    listing = collect_listing(
        fetcher, first_html, args.start_page, end_page, args.workers, args.limit
    )

    unique = {}
    for item in listing:
        unique.setdefault(item["url"], item)
    rows = list(unique.values())
    if args.limit:
        rows = rows[: args.limit]
    log(f"Collected {len(rows)} unique ROMs. Fetching detail pages...")

    try:
        fetch_rom_pages(fetcher, rows, args.workers)
    except KeyboardInterrupt:
        log("Interrupted - writing partial results...")

    write_csv(rows, args.output)

    failures = sum(1 for row in rows if row.get("erro"))
    log(
        f"Wrote {len(rows)} rows to {args.output}"
        + (f" ({failures} failed detail pages)" if failures else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
