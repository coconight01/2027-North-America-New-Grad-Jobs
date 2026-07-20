#!/usr/bin/env python3
"""Path-aware GitHub and optional Google Jobs discovery."""
from __future__ import annotations

import argparse, html, logging, os, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit

import requests, yaml
from bs4 import BeautifulSoup

from update_jobs import (
    CITIZEN, HEADERS, LOG, NO_SPONSOR, ROOT, SALARY, SKILLS, TIMEOUT, TODAY,
    YES_SPONSOR, citizen_required, dedupe, eligible, enrich,
    job, load_csv, write,
)

MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)]+)\)")
HTML_LINK = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.I)
BLOCKED_LINKS = ("github.com/", "simplify.jobs/p/")


def apply_source_hints(item, text):
    # Source-list symbols are hints only. Citizenship is decided from the
    # individual employer posting, not from a company-wide assumption.
    if "🛂" in text:
        item["sponsorship"] = "No"
        item["visa_evidence"] = "Source list marks no sponsorship"
    if "🔒" in text:
        item["status"] = "Closed"
    return item


def application_link(line):
    links = [url for _, url in MARKDOWN_LINK.findall(line)] + HTML_LINK.findall(line)
    return next((url for url in reversed(links) if not any(x in url for x in BLOCKED_LINKS)), "")


def text_cell(value):
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = value.replace("**", "").replace("`", "").replace("↳", "").strip()
    return BeautifulSoup(html.unescape(value), "html.parser").get_text(" ", strip=True)


def lever_description(entry):
    values = [
        entry.get("descriptionPlain"), entry.get("description"),
        entry.get("additionalPlain"), entry.get("additional"),
        entry.get("requirementsPlain"),
    ]
    for group in entry.get("lists") or []:
        values.extend([group.get("text"), group.get("content")])
    return " ".join(text_cell(value or "") for value in values if value)


def add_text_evidence(item, text):
    if not text:
        return item
    item["description"] = text[:80000]
    item["last_verified"] = TODAY
    match = CITIZEN.search(text)
    if match:
        item["citizenship_required"] = "Yes"
        item["visa_evidence"] = match.group(0)[:180]
    match = NO_SPONSOR.search(text)
    if match:
        item["sponsorship"] = "No"
        item["visa_evidence"] = match.group(0)[:180]
    elif (match := YES_SPONSOR.search(text)):
        item["sponsorship"] = "Yes"
        item["visa_evidence"] = match.group(0)[:180]
    ranges = list(SALARY.finditer(text))
    if ranges:
        match = next(
            (x for x in ranges if int(x.group(1).replace(",", "").split(".")[0]) >= 20000),
            ranges[0],
        )
        unit = (match.group(3) or "").lower()
        suffix = "/hr" if "hour" in unit or "/hr" in unit else ("/year" if unit else "")
        item["salary"] = f"${match.group(1)}–${match.group(2)}{suffix}"
    item["skills"] = ", ".join([name for name, pattern in SKILLS if pattern.search(text)][:10])
    return item


def enrich_lever_api(item):
    parsed = urlsplit(item.get("url", ""))
    if parsed.netloc not in {"jobs.lever.co", "jobs.eu.lever.co"}:
        return item
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return item
    host = "api.eu.lever.co" if parsed.netloc == "jobs.eu.lever.co" else "api.lever.co"
    try:
        response = requests.get(
            f"https://{host}/v0/postings/{parts[0]}/{parts[1]}?mode=json",
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return add_text_evidence(item, lever_description(response.json()))
    except (requests.RequestException, ValueError, KeyError) as exc:
        LOG.debug("Lever API detail failed for %s: %s", item.get("url"), exc)
        return item


def github_table(source):
    repo = source["repo"]
    ref = source.get("ref", "main")
    path = source.get("path", "README.md").lstrip("/")
    max_rows = max(1, int(source.get("max_rows", 1000)))
    assume_2027 = bool(source.get("assume_2027", False))
    response = requests.get(
        f"https://raw.githubusercontent.com/{repo}/{ref}/{path}",
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    response.raise_for_status()

    rows, previous_company = [], ""
    for line in response.text.splitlines():
        if len(rows) >= max_rows:
            break
        if line.count("|") < 4:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4 or set(cells[0]) <= {"-", ":"}:
            continue
        company, role, location = map(text_cell, cells[:3])
        if not role or role.casefold() in {"role", "position", "job title"}:
            continue
        previous_company = company or previous_company
        company = company or previous_company
        apply_url = application_link(line)
        if not company or not apply_url:
            continue
        item = apply_source_hints(
            job(company, role, location or "Unknown", apply_url, f"GitHub:{repo}:{path}"),
            line,
        )
        if assume_2027:
            item["graduation"], item["match"] = "2027", "Trusted 2027 source"
        if len(cells) >= 5:
            salary = text_cell(cells[3])
            if "$" in salary or re.search(r"\b\d{2,3}k/(?:yr|year)\b", salary, re.I):
                item["salary"] = salary
        rows.append(enrich_lever_api(item))
    return rows


def google_jobs(source):
    api_key = os.getenv(source.get("api_key_env", "SERPAPI_KEY"), "").strip()
    if not api_key:
        LOG.info("Google Jobs skipped: SERPAPI_KEY is not configured")
        return []
    rows, max_pages = [], max(1, min(int(source.get("max_pages", 1)), 3))
    for query in source.get("queries", []):
        next_token = None
        for _ in range(max_pages):
            params = {
                "engine": "google_jobs", "q": query["q"], "api_key": api_key,
                "hl": query.get("hl", "en"), "gl": query.get("gl", "us"),
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
                apply_url = next(
                    (x.get("link", "") for x in (result.get("apply_options") or []) if x.get("link")),
                    "",
                )
                if not apply_url:
                    continue
                description = " ".join([
                    result.get("description", ""),
                    " ".join(result.get("extensions") or []),
                ])
                rows.append(add_text_evidence(job(
                    result.get("company_name", "Unknown"),
                    result.get("title", ""),
                    result.get("location", "Unknown"),
                    apply_url,
                    "Google Jobs via SerpAPI",
                    description,
                ), description))
            next_token = (data.get("serpapi_pagination") or {}).get("next_page_token")
            if not next_token:
                break
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-detail-pages", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-github", action="store_true")
    parser.add_argument("--skip-google", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    config = yaml.safe_load((ROOT / "config/sources.yml").read_text(encoding="utf-8")) or {}
    discovered = []
    for source in ([] if args.skip_github else config.get("extra_github_discovery", []) or []):
        if not source.get("enabled", True):
            continue
        try:
            found = github_table(source)
            LOG.info("extra GitHub %s/%s: %d", source["repo"], source.get("path", "README.md"), len(found))
            discovered.extend(found)
        except Exception as exc:
            LOG.warning("extra GitHub %s failed: %s", source, exc)

    search = config.get("serpapi_google_jobs") or {}
    if not args.skip_google and search.get("enabled", False):
        try:
            found = google_jobs(search)
            LOG.info("Google Jobs via SerpAPI: %d", len(found))
            discovered.extend(found)
        except Exception as exc:
            LOG.warning("Google Jobs discovery failed: %s", exc)

    blocked_urls = {item.get("url", "") for item in discovered if citizen_required(item)}
    candidates = dedupe([item for item in discovered if eligible(item, False)])
    if candidates:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = {
                executor.submit(enrich, item): index
                for index, item in enumerate(candidates[: args.max_detail_pages])
            }
            for future in as_completed(futures):
                try:
                    candidates[futures[future]] = future.result()
                except Exception as exc:
                    LOG.warning("extra enrichment failed: %s", exc)
        blocked_urls.update(
            item.get("url", "") for item in candidates if citizen_required(item)
        )
        candidates = dedupe(
            [item for item in candidates if not citizen_required(item)]
        )

    previous = [
        item for item in load_csv(ROOT / "data/jobs.csv")
        if item.get("url", "") not in blocked_urls and not citizen_required(item)
    ]
    merged = dedupe(previous + candidates)
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
