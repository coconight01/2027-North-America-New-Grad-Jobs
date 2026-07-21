#!/usr/bin/env python3
"""Discover likely new-grad roles from selected official big-tech career pages.

Each source is isolated: a markup or network failure is logged and skipped. The
normal per-posting qualification pass later decides whether a role is truly
new-grad eligible.
"""
from __future__ import annotations

import argparse
import logging
import re
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

from update_jobs import (
    HEADERS, LOG, NEW_GRAD, ROOT, YEAR_2027, dedupe, job, load_csv, write,
)

OFFICIAL_SIGNAL = re.compile(
    r"\b(?:2027|new grad(?:uate)?|university grad(?:uate)?|college grad(?:uate)?|"
    r"early career|entry[- ]level|student programs?|recent grad(?:uate)?)\b",
    re.I,
)
AMAZON_JOB = re.compile(r"/en/jobs/\d+/", re.I)
APPLE_JOB = re.compile(r"/en-us/details/[^/]+/", re.I)


def card_for(anchor):
    for tag in ("article", "li", "section"):
        parent = anchor.find_parent(tag)
        if parent:
            return parent
    parent = anchor
    for _ in range(5):
        parent = parent.parent
        if not parent:
            break
        text = parent.get_text(" ", strip=True)
        if 80 <= len(text) <= 5000:
            return parent
    return anchor.parent or anchor


def title_for(anchor, card) -> str:
    text = anchor.get_text(" ", strip=True)
    if 3 <= len(text) <= 180:
        return text
    heading = card.find(["h1", "h2", "h3", "h4"])
    return heading.get_text(" ", strip=True) if heading else text


def amazon_location(text: str, fallback: str) -> str:
    match = re.search(
        r"([A-Za-z .'-]+,\s*(?:[A-Z]{2}|[A-Za-z .'-]+),\s*(?:USA|CAN))",
        text,
    )
    return match.group(1).strip() if match else fallback


def apple_location(text: str, fallback: str) -> str:
    match = re.search(
        r"\bLocation\s+(.{2,100}?)(?:\s+Actions\b|\s+Role Number\b|"
        r"\s+Weekly Hours\b|\s+Submit Resume\b|$)",
        text,
        re.I,
    )
    return match.group(1).strip(" -–—|") if match else fallback


def fetch_html(url: str, params: dict | None = None) -> BeautifulSoup:
    response = requests.get(url, params=params, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def discover_amazon() -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    searches = [
        ("2027 software development", "USA", "United States"),
        ("new grad software development", "USA", "United States"),
        ("2027 software development", "CAN", "Canada"),
        ("new grad software development", "CAN", "Canada"),
    ]
    for query, country, fallback in searches:
        params = {
            "base_query": query,
            "category[]": "software-development",
            "category_type": "studentprograms",
            "country": country,
            "result_limit": 100,
            "sort": "recent",
        }
        soup = fetch_html("https://www.amazon.jobs/en/search", params)
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href", "")
            if not AMAZON_JOB.search(href):
                continue
            url = urljoin("https://www.amazon.jobs", href)
            if url in seen:
                continue
            card = card_for(anchor)
            text = card.get_text(" ", strip=True)
            title = title_for(anchor, card)
            if not (OFFICIAL_SIGNAL.search(title) or OFFICIAL_SIGNAL.search(text)):
                continue
            seen.add(url)
            item = job(
                "Amazon",
                title,
                amazon_location(text, fallback),
                url,
                "Official:Amazon Jobs",
                text,
            )
            item["match"] = "Official big-tech new-grad search"
            rows.append(item)
    return rows


def discover_apple() -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    searches = [
        ("2027", "united-states-USA", "United States"),
        ("new grad", "united-states-USA", "United States"),
        ("early career", "united-states-USA", "United States"),
        ("2027", "canada-CANC", "Canada"),
        ("new grad", "canada-CANC", "Canada"),
    ]
    for query, location, fallback in searches:
        params = {"search": query, "location": location, "sort": "newest"}
        soup = fetch_html("https://jobs.apple.com/en-us/search", params)
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href", "")
            if not APPLE_JOB.search(href):
                continue
            url = urljoin("https://jobs.apple.com", href)
            if url in seen:
                continue
            card = card_for(anchor)
            text = card.get_text(" ", strip=True)
            title = title_for(anchor, card)
            if not (OFFICIAL_SIGNAL.search(title) or OFFICIAL_SIGNAL.search(text)):
                continue
            seen.add(url)
            item = job(
                "Apple",
                title,
                apple_location(text, fallback),
                url,
                "Official:Apple Careers",
                text,
            )
            item["match"] = "Official big-tech new-grad search"
            rows.append(item)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    discovered: list[dict] = []
    for name, function in [("Amazon", discover_amazon), ("Apple", discover_apple)]:
        try:
            rows = function()
            LOG.info("official %s: %d", name, len(rows))
            discovered.extend(rows)
        except Exception as exc:
            LOG.warning("official %s discovery failed: %s", name, exc)

    discovered = dedupe(discovered)
    if args.dry_run:
        print(f"Discovered {len(discovered)} official big-tech candidates")
        for item in discovered[:50]:
            print(item.get("company"), "-", item.get("role"), "-", item.get("location"))
        return 0

    previous = load_csv(ROOT / "data/jobs.csv")
    write(dedupe(previous + discovered))
    print(f"Merged {len(discovered)} official big-tech candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
