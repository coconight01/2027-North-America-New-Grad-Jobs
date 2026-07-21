#!/usr/bin/env python3
"""Apply date-first display ordering after all discovery steps finish.

The collector's ``date_added`` is the first date this repository discovered a
role. For ATS platforms that expose a reliable creation/publication timestamp,
this script records it as ``posted_date``. Otherwise it deliberately falls back
to ``date_added`` rather than presenting an update timestamp as a publication
date.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TODAY = date.today().isoformat()
TIMEOUT = 15
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; 2027-New-Grad-Jobs/0.5; "
        "+https://github.com/coconight01/2027-North-America-New-Grad-Jobs)"
    )
}


def clean_date(value: object) -> str:
    """Return YYYY-MM-DD for ISO strings or epoch timestamps."""
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    text = str(value).strip()
    if text.isdigit():
        return clean_date(int(text))
    match = re.match(r"(20\d{2})-(\d{2})-(\d{2})", text)
    if match:
        return match.group(0)
    return ""


def get_json(url: str) -> dict | list:
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def lever_posted_date(parsed) -> str:
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return ""
    host = "api.eu.lever.co" if parsed.netloc == "jobs.eu.lever.co" else "api.lever.co"
    data = get_json(f"https://{host}/v0/postings/{parts[0]}/{parts[1]}?mode=json")
    return clean_date(data.get("createdAt"))


def ashby_posted_date(parsed, cache: dict[str, dict[str, str]]) -> str:
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return ""
    board, posting_id = parts[0], parts[1]
    if board not in cache:
        data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
        mapping: dict[str, str] = {}
        for item in data.get("jobs", []):
            item_url = item.get("jobUrl") or item.get("applyUrl") or ""
            item_parts = [part for part in urlsplit(item_url).path.split("/") if part]
            item_id = item_parts[-1] if item_parts else ""
            posted = clean_date(item.get("publishedAt") or item.get("createdAt"))
            if item_id and posted:
                mapping[item_id] = posted
        cache[board] = mapping
    return cache[board].get(posting_id, "")


def smartrecruiters_posted_date(parsed) -> str:
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return ""
    company, posting_id = parts[0], parts[1]
    data = get_json(
        f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting_id}"
    )
    return clean_date(data.get("releasedDate") or data.get("createdOn"))


def fetch_posted_date(url: str, ashby_cache: dict[str, dict[str, str]]) -> str:
    parsed = urlsplit(url)
    try:
        if parsed.netloc in {"jobs.lever.co", "jobs.eu.lever.co"}:
            return lever_posted_date(parsed)
        if parsed.netloc == "jobs.ashbyhq.com":
            return ashby_posted_date(parsed, ashby_cache)
        if parsed.netloc == "jobs.smartrecruiters.com":
            return smartrecruiters_posted_date(parsed)
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return ""
    return ""


def integer(value: object) -> int:
    try:
        return int(float(str(value or "0").replace(",", "")))
    except (TypeError, ValueError):
        return 0


def date_key(job: dict) -> str:
    return job.get("posted_date") or job.get("date_added") or "0000-00-00"


def known_low_salary(job: dict) -> int:
    """Within a date, put known sub-$200k roles behind high/unknown-pay roles."""
    maximum = integer(job.get("salary_max_annual"))
    return 1 if maximum and maximum < 200_000 else 0


def sort_key(job: dict) -> tuple:
    # Ascending key with inverted numeric components. Python's ISO dates sort
    # lexicographically, so converting YYYYMMDD to a negative integer gives
    # newest-first ordering.
    day = integer(date_key(job).replace("-", ""))
    phd = 1 if str(job.get("phd_required", "")).casefold() == "yes" else 0
    low_pay = known_low_salary(job)
    score = integer(job.get("personalized_score"))
    salary = integer(job.get("salary_max_annual"))
    return (
        0 if job.get("status") == "Open" else 1,
        -day,
        phd,
        low_pay,
        -score,
        -salary,
        str(job.get("company", "")).casefold(),
        str(job.get("role", "")).casefold(),
    )


def load_rows() -> tuple[list[dict], list[str]]:
    path = DATA / "jobs.csv"
    if not path.exists():
        return [], []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader], list(reader.fieldnames or [])


def load_date_cache() -> dict[str, str]:
    path = DATA / "posted_dates.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {str(key): str(value) for key, value in raw.items() if clean_date(value)}
    except (json.JSONDecodeError, OSError, TypeError):
        return {}


def save(rows: list[dict], fields: list[str], date_cache: dict[str, str]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    if "posted_date" not in fields:
        insertion = fields.index("date_added") if "date_added" in fields else len(fields)
        fields.insert(insertion, "posted_date")

    with (DATA / "jobs.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    (DATA / "jobs.json").write_text(
        json.dumps([{field: row.get(field, "") for field in fields} for row in rows], indent=2)
        + "\n",
        encoding="utf-8",
    )
    (DATA / "posted_dates.json").write_text(
        json.dumps(dict(sorted(date_cache.items())), indent=2) + "\n",
        encoding="utf-8",
    )

    open_rows = [row for row in rows if row.get("status") == "Open"]
    parts = [
        "# 2027 North America New Grad Full-Time Jobs",
        "",
        f"> Last automated update: **{TODAY}** · Open roles: **{len(open_rows)}**",
        "",
        "Default order is newest posting/first-discovery date first. Within the same date, non-PhD-only and better-compensated roles appear earlier; personalized fit is the next tie-breaker.",
        "",
        "> Hard filters remove explicit no-sponsorship, U.S.-citizenship/security-clearance, pure-hardware, and stated salary ranges entirely below $100k.",
        "",
        "| Posted | Score | Company | Role | Category | Location | Visa | Salary | PhD | Why |",
        "|---|---:|---|---|---|---|---|---|---|---|",
    ]
    for row in open_rows:
        visa = "✅" if row.get("sponsorship") == "Yes" else "❔"
        phd = "PhD only" if row.get("phd_required") == "Yes" else "—"
        role_name = str(row.get("role", "")).replace("|", "/")
        role = f"[{role_name}]({row.get('url', '')})" if row.get("url") else role_name
        values = [
            date_key(row),
            f"**{row.get('personalized_score', 0)} · {row.get('priority', '')}**",
            f"**{str(row.get('company', '')).replace('|', '/')}**",
            role,
            str(row.get("category", "")).replace("|", "/"),
            str(row.get("location", "")).replace("|", "/"),
            visa,
            str(row.get("salary") or "Not listed").replace("|", "/"),
            phd,
            str(row.get("personalized_reason") or "—").replace("|", "/"),
        ]
        parts.append("| " + " | ".join(values) + " |")
    parts += [
        "",
        "## Date semantics",
        "",
        "- `posted_date`: ATS creation/publication date when the platform exposes one.",
        "- Otherwise `posted_date` falls back to this repository's first-discovery `date_added`.",
        "- ATS update timestamps are not presented as original publication dates.",
        "",
        "## Automatic updates",
        "",
        "GitHub Actions refreshes the data every six hours; Google Jobs discovery runs daily.",
        "",
        "Listings can close or change without notice. Verify all details before applying.",
    ]
    (ROOT / "README.md").write_text("\n".join(parts) + "\n", encoding="utf-8")


def main() -> None:
    rows, fields = load_rows()
    if not rows:
        print("No job rows to order.")
        return

    date_cache = load_date_cache()
    ashby_cache: dict[str, dict[str, str]] = {}
    fetched = 0
    for row in rows:
        url = row.get("url", "")
        posted = clean_date(row.get("posted_date")) or clean_date(date_cache.get(url))
        # Only make ATS calls for newly discovered jobs. Historical rows retain
        # their first-discovery date unless an ATS date was already cached.
        if not posted and row.get("date_added") == TODAY and url:
            posted = fetch_posted_date(url, ashby_cache)
            fetched += bool(posted)
        posted = posted or clean_date(row.get("date_added")) or TODAY
        row["posted_date"] = posted
        if url:
            date_cache[url] = posted

    rows.sort(key=sort_key)
    save(rows, fields, date_cache)
    print(f"Ordered {len(rows)} jobs by date first; fetched {fetched} ATS publication dates.")


if __name__ == "__main__":
    main()
