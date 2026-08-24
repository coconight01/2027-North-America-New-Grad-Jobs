#!/usr/bin/env python3
"""Second-stage, resume-aware reranker and Apply-Now shortlist generator."""
from __future__ import annotations

import argparse
import csv
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from profile_ranker import load_profile, norm, official_source, score_job, smart_sort_key

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TODAY = date.today().isoformat()
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; smart-job-ranker/1.1)"}
TIMEOUT = 18

NON_FTE_TITLE = re.compile(
    r"\b(?:intern(?:ship)?|co[- ]?op|fellowship|apprentice(?:ship)?|skillbridge|"
    r"returnship|externship|student researcher|student worker|part[- ]?time|contract(?:or|ing)?)\b",
    re.I,
)
EXPLICIT_2026 = re.compile(r"\b(?:2026|class of 2026|2026 start|new college grad(?:uate)? 2026)\b", re.I)
EXPLICIT_2027 = re.compile(r"\b(?:2027|class of 2027|2027 start|2027 grads?)\b", re.I)
SUSPICIOUS_COMPANIES = {"ecommerce guide"}


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
                chunks = [
                    data.get("descriptionPlain"), data.get("description"),
                    data.get("additionalPlain"), data.get("additional"), data.get("requirementsPlain"),
                ]
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


def company_group(value: object) -> str:
    company = norm(value)
    if company in {"bytedance", "tiktok", "tiktok usds jv", "beijing bytedance technology co ltd"}:
        return "bytedance-tiktok"
    if company in {"nvidia", "nvidia ai"}:
        return "nvidia"
    if company in {"susquehanna international group", "sig"}:
        return "sig"
    return company


def hard_veto(row: dict) -> str:
    role = str(row.get("role") or "")
    if NON_FTE_TITLE.search(role):
        return "non-full-time program title"
    if EXPLICIT_2026.search(role) and not EXPLICIT_2027.search(role):
        return "explicit 2026-only title"
    if norm(row.get("company")) in SUSPICIOUS_COMPANIES:
        return "untrusted aggregator company attribution"
    if row.get("citizenship_required") == "Yes":
        return "citizenship/security-clearance requirement"
    if row.get("sponsorship") == "No":
        return "explicit no sponsorship"
    return ""


def tracker_blocks(tracker: dict) -> tuple[set[str], set[str], set[str]]:
    exact, company_only, hold_groups = set(), set(), set()
    active_statuses = {"applied", "oa", "oa completed", "interview", "rejected", "offer"}
    for app in tracker.get("applications", []) or []:
        if str(app.get("status") or "").casefold() not in active_statuses:
            continue
        company = norm(app.get("company"))
        role = norm(app.get("role"))
        confidence = str(app.get("confidence") or "").casefold()
        note = " ".join([
            str(app.get("next_action") or ""),
            str(app.get("application_limit_note") or ""),
        ]).casefold()
        if (
            "do not recommend additional" in note
            or "keep all additional" in note
            or ("additional" in note and "on hold" in note)
        ):
            hold_groups.add(company_group(company))
        if confidence == "company-only" or "role uncertain" in role or "exact requisition uncertain" in role:
            if company:
                company_only.add(company)
        elif company and role:
            exact.add(company + "|" + role)
    return exact, company_only, hold_groups


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
    exact, company_only, hold_groups = tracker_blocks(tracker)
    chosen, counts = [], {}
    for row in sorted(rows, key=smart_sort_key):
        score = int(row.get("personalized_score") or 0)
        if score < threshold or hard_veto(row):
            continue
        if row.get("status") != "Open" or row.get("ng_confidence") == "Not NG":
            continue
        if company_group(row.get("company")) in hold_groups:
            continue
        if already_in_process(row, exact, company_only):
            continue
        if str(row.get("phd_required") or "").casefold() == "yes" and score < exceptional_phd:
            continue
        # Weak third-party rows need substantially more evidence before reaching Apply Now.
        if not official_source(row) and score < 84:
            continue
        if str(row.get("ng_confidence") or "Uncertain") == "Uncertain" and (
            score < 90 or not official_source(row)
        ):
            continue
        group = company_group(row.get("company"))
        if counts.get(group, 0) >= per_company:
            continue
        chosen.append(row)
        counts[group] = counts.get(group, 0) + 1
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
    if not official_source(row):
        caveats.append("current canonical source is third-party; verify employer page before applying")
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
        "application_limit": "Application tracker checked; exact/company-level in-process matches and explicit company holds are suppressed.",
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

    # Defense in depth: the full web data should not keep obvious internship/2026-only leakage.
    veto_counts = {}
    clean_rows = []
    for row in rows:
        reason = hard_veto(row)
        if reason:
            veto_counts[reason] = veto_counts.get(reason, 0) + 1
            continue
        clean_rows.append(row)
    rows = clean_rows

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
        "reviewer": "Automated resume-aware ranker v2.1",
        "candidate_focus": (profile.get("candidate") or {}).get("primary_tracks", []),
        "summary": (
            f"Ranked {len(rows)} full-time/2027-compatible candidates with two-stage resume-aware scoring; "
            f"{len(picks)} roles passed Apply-Now after application-tracker, company-hold, source-confidence, and false-positive guards."
        ),
        "changes_today": [
            {"type": "automated_rerank", "reason": "Fit bands now outrank raw recency; title-scoped mismatch signals prevent page-boilerplate penalties."},
            {"type": "quality_guard", "reason": f"Removed obvious non-FTE, explicit-2026-only, and suspicious-attribution rows before web output: {veto_counts}."},
        ],
        "recommendations": [recommendation(row, i + 1) for i, row in enumerate(picks)],
    }

    if args.dry_run:
        print("vetoes:", veto_counts)
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
    print(f"Smart-ranked {len(rows)} jobs; generated {len(picks)} Apply-Now recommendations; vetoed {sum(veto_counts.values())}")


if __name__ == "__main__":
    main()
