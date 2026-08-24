#!/usr/bin/env python3
"""Second-stage resume-aware prefilter that builds a review queue for human/LLM judgment."""
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
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; smart-job-ranker/1.4)"}
TIMEOUT = 18

NON_FTE_TITLE = re.compile(
    r"\b(?:intern(?:ship)?s?|co[- ]?op|fellowship|apprentice(?:ship)?|skillbridge|"
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
    if "bytedance" in company or "tiktok" in company:
        return "bytedance-tiktok"
    if company in {"nvidia", "nvidia ai"}:
        return "nvidia"
    if company in {"susquehanna international group", "sig"}:
        return "sig"
    if company in {"citadel", "citadel securities", "citadel / citadel securities"}:
        return "citadel"
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
    """Return exact role blocks, company-level possible matches, and explicit company holds."""
    exact, possible_company, hold_groups = set(), set(), set()
    active_statuses = {"applied", "oa", "oa completed", "interview", "rejected", "offer"}
    for app in tracker.get("applications", []) or []:
        if str(app.get("status") or "").casefold() not in active_statuses:
            continue
        company = company_group(app.get("company"))
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
            hold_groups.add(company)
        if confidence == "exact" and company and role:
            exact.add(company + "|" + role)
        elif company:
            possible_company.add(company)
    return exact, possible_company, hold_groups


def exact_application_match(job: dict, exact: set[str]) -> bool:
    company = company_group(job.get("company"))
    role = norm(job.get("role"))
    for item in exact:
        c, r = item.split("|", 1)
        if c != company:
            continue
        if r == role or (len(r) >= 14 and len(role) >= 10 and (r in role or role in r)):
            return True
    return False


def possible_company_application(job: dict, possible_company: set[str]) -> bool:
    return company_group(job.get("company")) in possible_company


def build_review_queue(rows: list[dict], profile: dict, tracker: dict) -> list[dict]:
    """Recall-oriented queue. Machine score prioritizes review but never becomes Apply Now by itself."""
    ranking = profile.get("ranking") or {}
    threshold = max(58, int(ranking.get("shortlist_min_score", 76)) - 18)
    limit = max(60, int(ranking.get("shortlist_size", 32)) * 3)
    per_company = max(4, int(ranking.get("max_per_company", 2)) * 2)
    exact, possible_company, hold_groups = tracker_blocks(tracker)
    chosen, counts = [], {}
    for row in sorted(rows, key=smart_sort_key):
        score = int(row.get("personalized_score") or 0)
        if score < threshold or hard_veto(row):
            continue
        if row.get("status") != "Open" or row.get("ng_confidence") == "Not NG":
            continue
        group = company_group(row.get("company"))
        if group in hold_groups or exact_application_match(row, exact):
            continue
        if counts.get(group, 0) >= per_company:
            continue
        row["application_match"] = "Company-only possible" if possible_company_application(row, possible_company) else "None"
        if row["application_match"] != "None":
            row["application_note"] = "⚠ Possible prior application at this company; verify the applicant portal before applying."
        else:
            row["application_note"] = ""
        chosen.append(row)
        counts[group] = counts.get(group, 0) + 1
        if len(chosen) >= limit:
            break
    return chosen


def queue_record(row: dict, rank: int, detail_text: str) -> dict:
    possible = str(row.get("application_match") or "None") != "None"
    return {
        "machine_rank": rank,
        "machine_score": int(row.get("personalized_score") or 0),
        "machine_tier": row.get("priority") or "",
        "machine_reason": row.get("personalized_reason") or "",
        "company": row.get("company", ""),
        "role": row.get("role", ""),
        "category": row.get("category", ""),
        "location": row.get("location", ""),
        "salary": row.get("salary") or "Not listed",
        "salary_max_annual": row.get("salary_max_annual", ""),
        "ng_confidence": row.get("ng_confidence") or "Uncertain",
        "ng_evidence": row.get("ng_evidence") or "",
        "sponsorship": row.get("sponsorship") or "Unknown",
        "phd_required": row.get("phd_required") or "Unknown",
        "posted_date": row.get("posted_date") or row.get("date_added") or "",
        "source": row.get("source") or "",
        "official_source": official_source(row),
        "url": row.get("url", ""),
        "jd_excerpt": detail_text[:5000],
        "application_match": row.get("application_match") or "None",
        "application_note": row.get("application_note") or "",
        "review_status": "Needs semantic review + application check" if possible else "Needs semantic review",
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

    tracker = load_json(DATA / "application_tracker.json", {})
    exact, possible_company, hold_groups = tracker_blocks(tracker)

    veto_counts = {}
    clean_rows = []
    exact_removed = 0
    for row in rows:
        reason = hard_veto(row)
        if reason:
            veto_counts[reason] = veto_counts.get(reason, 0) + 1
            continue
        if exact_application_match(row, exact):
            exact_removed += 1
            continue
        if possible_company_application(row, possible_company):
            row["application_match"] = "Company-only possible"
            row["application_note"] = "⚠ Possible prior application at this company; verify the applicant portal before applying."
        elif company_group(row.get("company")) in hold_groups:
            row["application_match"] = "Company hold"
            row["application_note"] = "Application history indicates a company-level application limit/hold; keep visible but do not recommend another application now."
        else:
            row["application_match"] = "None"
            row["application_note"] = ""
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
        reason = result["reason"]
        if row.get("application_note"):
            reason = row["application_note"] + "; " + reason
        row["personalized_reason"] = reason

    rows.sort(key=smart_sort_key)
    queue_rows = build_review_queue(rows, profile, tracker)
    by_url = {str(row.get("url") or ""): i for i, row in enumerate(rows)}
    queue = {
        "updated_at": TODAY,
        "purpose": "Machine-generated recall queue for semantic review. Presence here is NOT an application recommendation.",
        "review_policy": "Final Apply Now decisions must come from semantic JD review against the candidate's actual resume, eligibility, application history, and role quality. Exact tracked applications are removed; company-only/likely application evidence is flagged, not suppressed.",
        "candidate_focus": (profile.get("candidate") or {}).get("primary_tracks", []),
        "veto_counts": veto_counts,
        "exact_tracked_jobs_removed": exact_removed,
        "candidates": [
            queue_record(row, rank, detail_text.get(by_url.get(str(row.get("url") or ""), -1), ""))
            for rank, row in enumerate(queue_rows, start=1)
        ],
    }

    if args.dry_run:
        print("vetoes:", veto_counts, "exact tracked removed:", exact_removed)
        for item in queue["candidates"][:20]:
            print(item["machine_score"], item["company"], "-", item["role"], "::", item["application_match"], "::", item["machine_reason"])
        return

    jobs_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    csv_path = DATA / "jobs.csv"
    if csv_path.exists():
        with csv_path.open(encoding="utf-8", newline="") as handle:
            fields = csv.DictReader(handle).fieldnames or []
        for extra in ("personalized_score", "priority", "personalized_reason", "application_match", "application_note"):
            if extra not in fields:
                fields.append(extra)
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    active_urls = {str(row.get("url") or "") for row in rows if row.get("url")}
    pruned = {url: entry for url, entry in cache.items() if url in active_urls}
    cache_path.write_text(json.dumps(pruned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (DATA / "model_review_queue.json").write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Smart-ranked {len(rows)} jobs; queued {len(queue_rows)} for semantic review; "
        f"removed {exact_removed} exact tracked applications; vetoed {sum(veto_counts.values())}. "
        "Existing ai_recommendations.json was not modified."
    )


if __name__ == "__main__":
    main()
