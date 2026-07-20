#!/usr/bin/env python3
"""Additional path-aware GitHub and optional Google Jobs discovery.

This module deliberately reuses the main collector's normalization, eligibility,
enrichment, deduplication, and rendering logic. It is run after update_jobs.py.
"""
from __future__ import annotations

import argparse
import html
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import yaml
from bs4 import BeautifulSoup

from update_jobs import (
    HEADERS,
    LOG,
    ROOT,
    TIMEOUT,
    dedupe,
    eligible,
    enrich,
    job,
    load_csv,
    write,
)

MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)]+)\)")
HTML_LINK = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.I)
BLOCKED_LINKS = ("github.com/", "simplify.jobs/p/")


def application_link(line: str) -> str:
    links = [url for _, url in MARKDOWN_LINK.findall(line)]
    links.extend(HTML_LINK.findall(line))
    for url in reversed(links):
        if not any(blocked in url for blocked in BLOCKED_LINKS):
            return url
    return ""


def text_cell(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = value.replace("**", "").replace("`", "").replace("↳", "").strip()
    return BeautifulSoup(html.unescape(value), "html.parser").get_text(" ", strip=True)


def github_table(source: dict) -> list[dict]:
    repo = source["repo"]
    ref = source.get("ref", "main")
    path = source.get("path", "README.md").lstrip("/")
    max_rows = max(1, int(source.get("max_rows", 1000)))
    assume_2027 = bool(source.get("assume_2027", False))
    url = f"https://raw.githubusercontent.com/{repo}/{ref}/{path}"
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()

    rows: list[dict] = []
    previous_company = ""
    for line in response.text.splitlines():
        if len(rows) >= max_rows:
            break
        if line.count("|") < 4:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4 or set(cells[0]) <= {"-", ":"}:
            continue
        company = text_cell(cells[0])
        role = text_cell(cells[1])
        location = text_cell(cells[2])
        if not role or role.casefold() in {"role", "position", "job title"}:
            continue
        if company:
            previous_company = company
        else:
            company = previous_company
        apply_url = application_link(line)
        if not company or not apply_url:
            continue

        item = job(company, role, location or "Unknown", apply_url, f"GitHub:{repo}:{path}")
        if assume_2027:
            item["graduation"] = "2027"
            item["match"] = "Trusted 2027 source"
        if len(cells) >= 5:
            possible_salary = text_cell(cells[3])
            if "$" in possible_salary or re.search(r"\b\d{2,3}k/(?:yr|year)\b", possible_salary, re.I):
                item["salary"] = possible_salary
        rows.append(item)
    return rows


def google_jobs(source: dict) -> list[dict]:
    api_key = os.getenv(source.get("api_key_env", "SERPAPI_KEY"), "").strip()
    if not api_key:
        LOG.info("Google Jobs skipped: SERPAPI_KEY repository secret is not configured")
        return []

    rows: list[dict] = []
    max_pages = max(1, min(int(source.get("max_pages", 1)), 3))
    for query in source.get("queries", []):
        next_token = None
        for _ in range(max_pages):
            params = {
                "engine": "google_jobs",
                "q": query["q"],
                "api_key": api_key,
                "hl": query.get("hl", "en"),
                "gl": query.get("gl", "us"),
            }
            if query.get("location"):
                params["location"] = query["location"]
            if next_token:
                params["next_page_token"] = next_token
            response = requests.get(
                "https://serpapi.com/search.json",
                params=params,
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            for result in data.get("jobs_results", []):
                options = result.get("apply_options") or []
                apply_url = next(
                    (option.get("link", "") for option in options if option.get("link")),
                    "",
                )
                if not apply_url:
                    continue
                description = " ".join([
                    result.get("description", ""),
                    " ".join(result.get("extensions") or []),
                ])
                item = job(
                    result.get("company_name", "Unknown"),
                    result.get("title", ""),
                    result.get("location", "Unknown"),
                    apply_url,
                    "Google Jobs via SerpAPI",
                    description,
                )
                rows.append(item)
            next_token = (data.get("serpapi_pagination") or {}).get("next_page_token")
            if not next_token:
                break
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-detail-pages", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-github", action="store_true")
    parser.add_argument("--skip-google", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    config = yaml.safe_load((ROOT / "config/sources.yml").read_text(encoding="utf-8")) or {}
    discovered: list[dict] = []

    for source in ([] if args.skip_github else (config.get("extra_github_discovery", []) or [])):
        if not source.get("enabled", True):
            continue
        try:
            found = github_table(source)
            LOG.info("extra GitHub %s/%s: %d", source["repo"], source.get("path", "README.md"), len(found))
            discovered.extend(found)
        except Exception as exc:
            LOG.warning("extra GitHub %s failed: %s", source, exc)

    search_config = config.get("serpapi_google_jobs") or {}
    if not args.skip_google and search_config.get("enabled", False):
        try:
            found = google_jobs(search_config)
            LOG.info("Google Jobs via SerpAPI: %d", len(found))
            discovered.extend(found)
        except Exception as exc:
            LOG.warning("Google Jobs discovery failed: %s", exc)

    candidates = dedupe([item for item in discovered if eligible(item, False)])
    if candidates:
        targets = candidates[: args.max_detail_pages]
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = {executor.submit(enrich, item): index for index, item in enumerate(targets)}
            for future in as_completed(futures):
                try:
                    candidates[futures[future]] = future.result()
                except Exception as exc:
                    LOG.warning("extra enrichment failed: %s", exc)

    merged = dedupe(load_csv(ROOT / "data/jobs.csv") + candidates)
    if args.dry_run:
        print(f"Discovered {len(candidates)} eligible extra jobs; merged total {len(merged)}")
        for item in candidates[:30]:
            print(item["company"], "-", item["role"], "-", item["location"])
        return 0
    write(merged)
    print(f"Merged {len(candidates)} extra jobs; total {len(merged)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
