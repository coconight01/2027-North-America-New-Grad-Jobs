#!/usr/bin/env python3
"""Second-stage, resume-aware reranker and Apply-Now shortlist generator."""
from __future__ import annotations

import argparse
import csv
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from profile_ranker import load_profile, norm, score_job, smart_sort_key

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TODAY = date.today().isoformat()
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; smart-job-ranker/1.0)"}
TIMEOUT = 18


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def fetch_text(url: str) -> str:
    if not url:
        return ""
    parsed = urlsplit(url)
    try:
        if parsed.netloc in {"jobs.lever.co", "jobs.eu.lever.co"}:
            parts = [x for x in parsed.path.split("/") if x]
            if len(parts) >= 2:
                host = "api.eu.lever.co" if parsed.netloc == "jobs.eu.lever.co" else "api.lever.co"
                response = requests.get(
                    f"https://{host}/v0/postings/{parts[0]}/{parts[1]}?mode=json",
                    headers=HEADERS, timeout=TIMEOUT,
                )
                response.raise_for_status()
                data = response.json()
                chunks = [data.get("descriptionPlain"), data.get("description"), data.get("additionalPlain"), data.get("additional")]
                for group in data.get("lists") or []:
                    chunks.extend([group.get("text"), group.get("content")])
                return BeautifulSoup(" ".join(x or "" for x in chunks), "html.parser").get_text(" ", strip=True)[:18000]
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if response.status_code >= 400:
            return ""
        return BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)[:18000]
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return ""


def cache_fresh(entry: dict, days: int) -> bool:
    try:
        return date.fromisoformat(str(entry.get("checked_at") or "")) >= date.today() - timedelta(days=days)
    except ValueError:
        return False


def tracker_blocks(tracker: dict) -> tuple[set[str], set[str]]:
    exact, company_only = set(), set()
    active_statuses = {"applied", "oa", "oa completed", "interview", "rejected", "offer"}
    for app in tracker.get("applications", []) or []:
        if str(app.get("status") or "").casefold() not in active_statuses:
            continue
        company = norm(app.get("company"))
        role = norm(app.get("role"))
        confidence = str(app.get("confidence") or "").casefold()
        if confidence == "company-only" or "role uncertain" in role or "exact requisition uncertain" in role:
            if company:
                company_only.add(company)
        elif company and role:
            exact.add(company + "|" + role)
    return exact, company_only


def already_in_process(job: dict, exact: set[str], company_only: set[str]) -> bool:
    company = norm(job.get("company"))
    role = norm(job.get("role"))
    if company in company_only:
        return True
    for item in exact:
        c, r = item.split("|", 1)
        if c != company:
            continue
        if r == role or (len(r) >= 14 and (r in role or role in r)):
            return True
    return False


def shortlist(rows: list[dict], profile: dict, tracker: dict) -> list[dict]:
    ranking = profile.get("ranking") or {}
    threshold = int(ranking.get("shortlist_min_score", 76))
    limit = int(ranking.get("shortlist_size", 32))
    per_company = int(ranking.get("max_per_company", 2))
    exceptional_phd = int(ranking.get("exceptional_phd_score", 93))
    exact, company_only = tracker_blocks(tracker)
    chosen, counts = [], {}
    for row in sorted(rows, key=smart_sort_key):
        score = int(row.get("personalized_score") or 0)
        if score < threshold:
            continue
        if row.get("status") != "Open" or row.get("ng_confidence") == "Not NG":
            continue
        if already_in_process(row, exact, company_only):
            continue
        if str(row.get("phd_required") or "").casefold() == "yes" and score < exceptional_phd:
            continue
        company = norm(row.get("company"))
        if counts.get(company, 0) >= per_company:
            continue
        chosen.append(row)
        counts[company] = counts.get(company, 0) + 1
        if len(chosen) >= limit:
            break
    return chosen


def recommendation(row: dict, rank: int) -> dict:
    score = int(row.get("personalized_score") or 0)
    ng = row.get("ng_confidence") or "Review"
    caveats = []
    if ng == "Uncertain":
        caveats.append("2027/new-grad eligibility still needs employer-page confirmation")
    if not row.get("salary_max_annual"):
        caveats.append("compensation is not reliably stated")
    if row.get("sponsorship") != "Yes":
        caveats.append("sponsorship is not explicitly stated")
    return {
        "rank": rank,
        "category": "AUTO · RESUME-AWARE",
        "company": row.get("company", ""),
        "role": row.get("role", ""),
        "location": row.get("location", ""),
        "salary": row.get("salary") or "Not listed",
        "fit": f"{score}/100 · {row.get('priority', '')}",
        "reason": row.get("personalized_reason") or "Strong profile overlap",
        "new_grad": f"{ng}: {row.get('ng_evidence') or 'current pipeline assessment'}",
        "sponsorship": "Sponsorship stated" if row.get("sponsorship") == "Yes" else "Not stated; no explicit veto in current data",
        "urgency": "APPLY FIRST" if score >= 90 else "APPLY SOON" if score >= 82 else "GOOD BACKLOG",
        "caveat": "; ".join(caveats),
        "application_limit": "Application tracker checked; exact/company-level in-process matches are suppressed.",
        "url": row.get("url", ""),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-detail-pages", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    profile = load_profile()
    jobs_path = DATA / "jobs.json"
    rows = load_json(jobs_path, [])
    if not rows:
        raise SystemExit("data/jobs.json is empty")

    cache_path = DATA / "profile_fit_cache.json"
    cache = load_json(cache_path, {})
    ranking = profile.get("ranking") or {}
    cache_days = int(ranking.get("detail_cache_days", 7))
    fetch_limit = args.max_detail_pages or int(ranking.get("detail_fetch_limit", 120))

    prelim = []
    for index, row in enumerate(rows):
        assessment = score_job(row, profile, "")
        prelim.append((assessment["score"], -assessment["freshness_days"], index))
    detail_indexes = [item[2] for item in sorted(prelim, reverse=True)[:max(0, fetch_limit)]]

    pending, detail_text = {}, {}
    for index in detail_indexes:
        row = rows[index]
        url = str(row.get("url") or "")
        entry = cache.get(url, {}) if url else {}
        if entry and cache_fresh(entry, cache_days):
            detail_text[index] = str(entry.get("text") or "")
        elif url:
            pending[index] = url

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_text, url): index for index, url in pending.items()}
        for future in as_completed(futures):
            index = futures[future]
            try:
                text = future.result()
            except Exception:
                text = ""
            detail_text[index] = text
            url = str(rows[index].get("url") or "")
            if url:
                cache[url] = {"checked_at": TODAY, "text": text[:12000]}

    for index, row in enumerate(rows):
        result = score_job(row, profile, detail_text.get(index, ""))
        row["personalized_score"] = result["score"]
        row["priority"] = result["tier"]
        row["personalized_reason"] = result["reason"]

    rows.sort(key=smart_sort_key)
    tracker = load_json(DATA / "application_tracker.json", {})
    picks = shortlist(rows, profile, tracker)
    auto = {
        "updated_at": TODAY,
        "reviewer": "Automated resume-aware ranker v2",
        "candidate_focus": (profile.get("candidate") or {}).get("primary_tracks", []),
        "summary": f"Automatically ranked {len(rows)} active candidates with two-stage resume-aware scoring; {len(picks)} roles passed the Apply-Now threshold after application-tracker suppression.",
        "changes_today": [{"type": "automated_rerank", "reason": "Freshness-bucket + resume-overlap ranking replaced raw keyword/date-only ordering for personalized scores."}],
        "recommendations": [recommendation(row, i + 1) for i, row in enumerate(picks)],
    }

    if args.dry_run:
        for row in picks[:15]:
            print(row.get("personalized_score"), row.get("company"), "-", row.get("role"), "::", row.get("personalized_reason"))
        return

    jobs_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    csv_path = DATA / "jobs.csv"
    if csv_path.exists():
        with csv_path.open(encoding="utf-8", newline="") as handle:
            fields = csv.DictReader(handle).fieldnames or []
        for extra in ("personalized_score", "priority", "personalized_reason"):
            if extra not in fields:
                fields.append(extra)
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    active_urls = {str(row.get("url") or "") for row in rows if row.get("url")}
    pruned = {url: entry for url, entry in cache.items() if url in active_urls}
    cache_path.write_text(json.dumps(pruned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (DATA / "ai_recommendations.json").write_text(json.dumps(auto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Smart-ranked {len(rows)} jobs; generated {len(picks)} Apply-Now recommendations")


if __name__ == "__main__":
    main()
