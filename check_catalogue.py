#!/usr/bin/env python3
"""
Fast pre-flight check for the catalogue refresh.

Fetches https://romsportugues.com/todos-jogos/ and counts
`.letter-section li` — the same set returned by the browser expression:

    document.querySelectorAll('.letter-section li').length

If that count differs from the number of rows in roms.csv, there may be new
data and the workflow should scrape. If it matches, the workflow stops with
"No new data" without touching the scraper.

GitHub Actions outputs (written when $GITHUB_OUTPUT is set):
    new_data=true|false
    remote_count=<n>
    local_count=<n>

Exit code is 0 for a successful check (whether or not data changed), and 1
for a fetch/parse failure.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

DEFAULT_URL = "https://romsportugues.com/todos-jogos/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class SectionCounter(HTMLParser):
    """Count <li> elements that have a .letter-section ancestor."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list = []
        self.count = 0

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "").split()
        is_section = tag == "div" and "letter-section" in classes
        self.stack.append((tag, is_section))
        if tag == "li" and any(flag for _, flag in self.stack):
            self.count += 1

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return


def br_number(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def fetch(url: str, timeout: float, retries: int) -> str:
    last_error = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, "replace")
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code in (429, 500, 502, 503, 504):
                time.sleep(min(30.0, 2.0 ** attempt))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt < retries:
                time.sleep(min(30.0, 2.0 ** attempt))
    raise last_error  # type: ignore[misc]


def count_section_items(html: str) -> int:
    parser = SectionCounter()
    parser.feed(html)
    parser.close()
    return parser.count


def count_csv_rows(path: Path) -> int:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def write_github_outputs(new_data: bool, remote: int, local: int) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"new_data={'true' if new_data else 'false'}\n")
        handle.write(f"remote_count={remote}\n")
        handle.write(f"local_count={local}\n")


def write_summary(new_data: bool, remote: int, local: int) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        if new_data:
            handle.write("### New data detected\n\n")
            handle.write(f"- Public `todos-jogos` list: **{br_number(remote)}** ROMs\n")
            handle.write(f"- Local `roms.csv`: **{br_number(local)}** ROMs\n")
            handle.write("- Starting the full scrape.\n")
        else:
            handle.write("### No new data\n\n")
            handle.write(f"- Public `todos-jogos` list: **{br_number(remote)}** ROMs\n")
            handle.write(f"- Local `roms.csv`: **{br_number(local)}** ROMs\n")
            handle.write("- **Nothing was scraped.**\n")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Check todos-jogos count against roms.csv before scraping.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--csv", default="roms.csv", help="local CSV path")
    parser.add_argument("--url", default=DEFAULT_URL, help="public catalogue URL")
    parser.add_argument("--timeout", type=float, default=30.0, help="request timeout")
    parser.add_argument("--retries", type=int, default=3, help="retries per request")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"error: CSV not found: {csv_path}", file=sys.stderr)
        return 1

    try:
        html = fetch(args.url, args.timeout, args.retries)
        remote = count_section_items(html)
        local = count_csv_rows(csv_path)
    except Exception as error:  # noqa: BLE001
        print(f"error: could not check catalogue: {error}", file=sys.stderr)
        return 1

    if remote == 0:
        print(
            "error: parsed 0 items from the public page; its structure may have changed",
            file=sys.stderr,
        )
        return 1

    new_data = remote != local
    print(f"Public list: {br_number(remote)} ROMs")
    print(f"Local CSV:   {br_number(local)} ROMs")

    if new_data:
        delta = remote - local
        print(f"New data detected ({delta:+d}).")
    else:
        print("No new data.")

    write_github_outputs(new_data, remote, local)
    write_summary(new_data, remote, local)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
