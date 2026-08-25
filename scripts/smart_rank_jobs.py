#!/usr/bin/env python3
"""Second-stage resume-aware prefilter that builds a review queue for human/LLM judgment."""
from __future__ import annotations

import argparse
import csv
import hashlib
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
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; smart-job-ranker/1.5)"}
TIMEOUT = 18

NON_FTE_TITLE = re.compile(
    r"\b(?:intern(?:ship)?s?|co[- ]?op|fellowship|apprentice(?:ship)?|skillbridge|"
    r"returnship|externship|student researcher|student worker|part[- ]?time|contract(?:or|ing)?)\b",
    re.I,
)
EXPLICIT_2026 = re.compile(r"\b(?:2026|class of 2026|2026 start|new college grad(?:uate)? 2026)\b", re.I)
EXPLICIT_2027 = re.compile(r"\b(?:2027|class of 2027|2027 start|2027 grads?)\b", re.I)
HIGH_POTENTIAL_TITLE = re.compile(
    r"\b(?:ml systems?|machine learning systems?|ai infrastructure|ml infrastructure|"
    r"inference|serving|runtime|compiler|performance|distributed|systems?|infrastructure|"
    r"platform|gpu|cuda|kernel|network(?:ing)?|storage|database|training|research engineer|"
    r"quantitative|trading|low latency)\b",
    re.I,
)
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


def company_priority(value: object, profile: dict) -> str:
    """Return Tier A / Tier B / Quant / empty using forgiving company-name matching."""
    company = norm(value)
    watch = profile.get("priority_company_watchlist") or {}
    for key, label in (("tier_a", "Tier A"), ("quant", "Quant"), ("tier_b", "Tier B")):
        for configured in watch.get(key, []) or []:
            candidate = norm(configured)
            if not candidate:
                continue
            if company == candidate or candidate in company or company in candidate:
                return label
    return ""


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


def _job_date(row: dict) -> date:
    for key in ("posted_date", "date_added"):
        try:
            return date.fromisoformat(str(row.get(key) or "")[:10])
        except ValueError:
            pass
    return date.min


def _take(selected: list[int], seen: set[int], ordered: list[int], quota: int) -> int:
    taken = 0
    for index in ordered:
        if taken >= quota:
            break
        if index in seen:
            continue
        selected.append(index)
        seen.add(index)
        taken += 1
    return taken


def select_detail_indexes(rows: list[dict], profile: dict, fetch_limit: int) -> tuple[list[int], dict]:
    """Recall-oriented sampling for full JD reads instead of blindly taking the top N scores."""
    if fetch_limit <= 0:
        return [], {}
    ranking = profile.get("ranking") or {}
    sampling = ranking.get("detail_sampling") or {}
    eligible_indexes = [
        i for i, row in enumerate(rows)
        if row.get("url") and row.get("application_match") != "Company hold"
    ]
    if not eligible_indexes:
        return [], {}

    assessments = {i: score_job(rows[i], profile, "") for i in eligible_indexes}
    score_order = sorted(
        eligible_indexes,
        key=lambda i: (
            int(assessments[i]["score"]),
            -int(assessments[i]["freshness_days"]),
            _job_date(rows[i]),
        ),
        reverse=True,
    )
    new_days = int(sampling.get("new_job_days", 5))
    cutoff = date.today() - timedelta(days=new_days)
    new_order = sorted(
        [i for i in eligible_indexes if _job_date(rows[i]) >= cutoff],
        key=lambda i: (_job_date(rows[i]), int(assessments[i]["score"])),
        reverse=True,
    )
    tier_rank = {"Tier A": 3, "Quant": 2, "Tier B": 1, "": 0}
    priority_order = sorted(
        [i for i in eligible_indexes if company_priority(rows[i].get("company"), profile)],
        key=lambda i: (
            tier_rank.get(company_priority(rows[i].get("company"), profile), 0),
            int(assessments[i]["score"]),
            _job_date(rows[i]),
        ),
        reverse=True,
    )
    title_order = sorted(
        [i for i in eligible_indexes if HIGH_POTENTIAL_TITLE.search(str(rows[i].get("role") or ""))],
        key=lambda i: (int(assessments[i]["score"]), _job_date(rows[i])),
        reverse=True,
    )
    exploration_order = sorted(
        eligible_indexes,
        key=lambda i: hashlib.sha1(
            (TODAY + "|" + str(rows[i].get("url") or i)).encode("utf-8")
        ).hexdigest(),
    )

    fractions = {
        "top_score": float(sampling.get("top_score_fraction", 0.42)),
        "new_jobs": float(sampling.get("new_job_fraction", 0.22)),
        "priority_companies": float(sampling.get("priority_company_fraction", 0.20)),
        "high_potential_titles": float(sampling.get("high_potential_title_fraction", 0.10)),
        "exploration": float(sampling.get("exploration_fraction", 0.06)),
    }
    quotas = {name: max(0, round(fetch_limit * frac)) for name, frac in fractions.items()}
    # Avoid rounding above the hard network/detail budget.
    while sum(quotas.values()) > fetch_limit:
        name = max(quotas, key=quotas.get)
        quotas[name] -= 1

    selected: list[int] = []
    seen: set[int] = set()
    actual = {}
    actual["top_score"] = _take(selected, seen, score_order, quotas["top_score"])
    actual["new_jobs"] = _take(selected, seen, new_order, quotas["new_jobs"])
    actual["priority_companies"] = _take(selected, seen, priority_order, quotas["priority_companies"])
    actual["high_potential_titles"] = _take(selected, seen, title_order, quotas["high_potential_titles"])
    actual["exploration"] = _take(selected, seen, exploration_order, quotas["exploration"])

    # Empty/overlapping buckets never waste capacity: fill remaining slots by broad score order,
    # then by exploration order so every requested detail slot is used when possible.
    actual["fill"] = _take(selected, seen, score_order, fetch_limit - len(selected))
    if len(selected) < fetch_limit:
        actual["fill"] += _take(selected, seen, exploration_order, fetch_limit - len(selected))

    metadata = {
        "limit": fetch_limit,
        "selected": len(selected),
        "configured_quotas": quotas,
        "actual_unique_selections": actual,
        "priority_company_candidates": len(priority_order),
        "new_job_candidates": len(new_order),
        "high_potential_title_candidates": len(title_order),
    }
    return selected, metadata


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


def queue_record(row: dict, rank: int, detail_text: str, profile: dict) -> dict:
    possible = str(row.get("application_match") or "None") != "None"
    return {
        "machine_rank": rank,
        "machine_score": int(row.get("personalized_score") or 0),
        "machine_tier": row.get("priority") or "",
        "machine_reason": row.get("personalized_reason") or "",
        "company_priority": company_priority(row.get("company"), profile),
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

    detail_indexes, sampling_metadata = select_detail_indexes(rows, profile, fetch_limit)

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
        company_tier = company_priority(row.get("company"), profile)
        if company_tier:
            reason = f"{company_tier} watchlist; " + reason
        if row.get("application_note"):
            reason = row["application_note"] + "; " + reason
        row["personalized_reason"] = reason

    rows.sort(key=smart_sort_key)
    queue_rows = build_review_queue(rows, profile, tracker)
    by_url = {str(row.get("url") or ""): i for i, row in enumerate(rows)}
    queue = {
        "updated_at": TODAY,
        "purpose": "Machine-generated recall queue for semantic review. Presence here is NOT an application recommendation.",
        "review_policy": "Final Apply Now decisions must come from semantic JD review against the candidate's actual resume, eligibility, application history, and role quality. Exact tracked applications are removed; company-only/likely application evidence is flagged, not suppressed. Priority-company roles receive dedicated JD-review quota so elite employers are not lost to weak titles.",
        "candidate_focus": (profile.get("candidate") or {}).get("primary_tracks", []),
        "detail_sampling": sampling_metadata,
        "veto_counts": veto_counts,
        "exact_tracked_jobs_removed": exact_removed,
        "candidates": [
            queue_record(row, rank, detail_text.get(by_url.get(str(row.get("url") or ""), -1), ""), profile)
            for rank, row in enumerate(queue_rows, start=1)
        ],
    }

    if args.dry_run:
        print("detail sampling:", sampling_metadata)
        print("vetoes:", veto_counts, "exact tracked removed:", exact_removed)
        for item in queue["candidates"][:20]:
            print(item["machine_score"], item["company_priority"], item["company"], "-", item["role"], "::", item["application_match"], "::", item["machine_reason"])
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
        f"Smart-ranked {len(rows)} jobs; detail-sampled {len(detail_indexes)} with recall buckets; "
        f"queued {len(queue_rows)} for semantic review; removed {exact_removed} exact tracked applications; "
        f"vetoed {sum(veto_counts.values())}. Existing ai_recommendations.json was not modified."
    )


if __name__ == "__main__":
    main()
