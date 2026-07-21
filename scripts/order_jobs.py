#!/usr/bin/env python3
"""Post-process jobs for new-grad confidence, hard filters, dates, and display order."""
from __future__ import annotations

import csv
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TODAY = date.today().isoformat()
TIMEOUT = 18
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; 2027-New-Grad-Jobs/0.6; "
        "+https://github.com/coconight01/2027-North-America-New-Grad-Jobs)"
    )
}

NEW_GRAD = re.compile(
    r"\b(new grad(uate)?|new college grad|university grad(uate)?|college grad|"
    r"entry[- ]level|early career|recent grad(uate)?|campus hire|"
    r"graduate (software|quantitative|machine learning|data|engineer|trader|"
    r"researcher)|software engineer i\b|engineer i\b)\b",
    re.I,
)
NG_BODY = re.compile(
    r"\b(new grad(uate)?|recent grad(uate)?|early career|entry[- ]level|"
    r"university recruiting|campus recruiting|graduating between|"
    r"currently pursuing (?:a |an )?(?:bachelor|master)|"
    r"bachelor'?s or master'?s degree.{0,80}(?:2026|2027))\b",
    re.I,
)
YEAR_2027 = re.compile(r"\b(2027|class of 2027|2026[-–/]2027)\b", re.I)
TRUSTED_NG_SOURCE = re.compile(
    r"(?:new[-_ ]?grad|college[-_ ]?jobs|new[-_ ]?college|"
    r"2027[-_/ ].*(?:swe|college|graduate)|"
    r"(?:swe|software|data|quant).*(?:new[-_ ]?grad))",
    re.I,
)
OBVIOUS_SENIOR_TITLE = re.compile(
    r"\b(?:senior(?!\s+(?:year|student))|sr\.?|staff|principal|director|lead|"
    r"head|vice president|v\.?p\.?|chief|architect|distinguished|fellow)\b",
    re.I,
)
PURE_HARDWARE = re.compile(
    r"\b(electrical|mechanical|manufacturing|asic|rtl|fpga|physical design|"
    r"silicon design|circuit|analog|mixed[- ]signal|pcb|board design|"
    r"semiconductor|verification engineer|validation engineer|embedded hardware|"
    r"firmware engineer|test hardware|hardware engineer)\b",
    re.I,
)
SOFTWARE_RESCUE = re.compile(
    r"\b(software|machine learning|ml systems?|ai infrastructure|"
    r"distributed systems?|compiler|runtime|cuda|gpu software|kernel|"
    r"systems programming|inference|training platform|cloud|database|network)\b",
    re.I,
)
NO_SPONSOR = re.compile(
    r"\b(?:will not|do not|does not|cannot|can't|unable to) "
    r"(?:provide|offer)?\s*(?:employment |work |visa )?sponsorship|"
    r"without (?:current or future )?sponsorship|"
    r"not eligible for (?:visa )?sponsorship|"
    r"(?:no|not available for) (?:employment |visa )?sponsorship\b",
    re.I,
)
YES_SPONSOR = re.compile(
    r"\b(?:visa|immigration|employment) sponsorship (?:is )?"
    r"(?:available|provided|offered)|(?:we|company) (?:will|can) sponsor|"
    r"support (?:for )?work authorization\b",
    re.I,
)
CITIZEN = re.compile(
    r"\b(?:u\.?s\.?|united states) citizenship (?:is )?"
    r"(?:required|mandatory)|must be (?:a )?(?:u\.?s\.?|united states) citizen|"
    r"only (?:u\.?s\.?|united states) citizens|"
    r"active (?:top secret|secret|ts/?sci|security) clearance|"
    r"(?:ability|eligible|eligibility) to obtain (?:and maintain )?"
    r"(?:a |an )?(?:top secret|secret|ts/?sci|security) clearance|"
    r"(?:position appropriate|current) security clearance (?:is )?required\b",
    re.I,
)
EXPLICIT_WORK_EXPERIENCE = re.compile(
    r"(?P<years>\d{1,2})(?:\s*[-–]\s*\d{1,2})?\s*\+?\s*years?\s+"
    r"(?:of\s+)?(?:professional|industry|full[- ]time|commercial|"
    r"post[- ]graduate|post[- ]degree|relevant\s+work|work)\b.{0,45}?experience|"
    r"(?P<years2>\d{1,2})(?:\s*[-–]\s*\d{1,2})?\s*\+?\s*years?\s+"
    r"(?:of\s+)?experience\s+(?:working\s+professionally|in\s+(?:the\s+)?industry|"
    r"in\s+a\s+professional\s+setting)",
    re.I,
)
GENERIC_EXPERIENCE = re.compile(
    r"(?P<years>\d{1,2})(?:\s*[-–]\s*\d{1,2})?\s*\+?\s*years?\s+"
    r"(?:of\s+)?experience(?:\s+in|\s+with|\b)",
    re.I,
)
SKILL_EXPERIENCE = re.compile(
    r"\b(programming|coding|coursework|academic|school|research|personal project|"
    r"open[- ]source)\b.{0,40}\bexperience\b|"
    r"\bexperience\b.{0,40}\b(programming|coding|coursework|academic|school|"
    r"research|personal project|open[- ]source)\b",
    re.I,
)
PHD_REQUIRED = re.compile(
    r"(?:\bph\.?d\.?\b|\bdoctoral degree\b).{0,45}"
    r"(?:required|must|required qualification|minimum qualification)|"
    r"(?:required|must have|minimum qualification).{0,60}"
    r"(?:\bph\.?d\.?\b|\bdoctoral degree\b)|\bphd only\b",
    re.I,
)
SALARY_RANGE = re.compile(
    r"(?:\$|USD\s*)\s*(?P<low>\d{2,3}(?:,\d{3})?(?:\.\d+)?)\s*(?P<lowk>[kK])?\s*"
    r"(?:-|–|—|to)\s*(?:\$|USD\s*)?\s*"
    r"(?P<high>\d{2,3}(?:,\d{3})?(?:\.\d+)?)\s*(?P<highk>[kK])?"
    r"(?:\s*(?P<unit>per year|annually|/year|per hour|hourly|/hr))?",
    re.I,
)


def clean_text(value: str) -> str:
    return BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)


def clean_date(value: object) -> str:
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
    return match.group(0) if match else ""


def get_json(url: str):
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def lever_payload(parsed):
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    host = "api.eu.lever.co" if parsed.netloc == "jobs.eu.lever.co" else "api.lever.co"
    return get_json(f"https://{host}/v0/postings/{parts[0]}/{parts[1]}?mode=json")


def lever_text(data: dict) -> str:
    values = [
        data.get("descriptionPlain"), data.get("description"),
        data.get("additionalPlain"), data.get("additional"),
        data.get("requirementsPlain"),
    ]
    for group in data.get("lists") or []:
        values.extend([group.get("text"), group.get("content")])
    return " ".join(clean_text(value or "") for value in values if value)


def fetch_job_text(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    try:
        if parsed.netloc in {"jobs.lever.co", "jobs.eu.lever.co"}:
            data = lever_payload(parsed)
            return lever_text(data or {}), clean_date((data or {}).get("createdAt"))
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        response.raise_for_status()
        return clean_text(response.text), ""
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return "", ""


def salary_from_text(text: str) -> tuple[str, int, int]:
    candidates = []
    for match in SALARY_RANGE.finditer(text):
        low = float(match.group("low").replace(",", ""))
        high = float(match.group("high").replace(",", ""))
        if match.group("lowk"):
            low *= 1000
        if match.group("highk"):
            high *= 1000
        unit = (match.group("unit") or "").lower()
        if "hour" in unit or "/hr" in unit:
            low *= 2080
            high *= 2080
        if max(low, high) < 20_000:
            continue
        candidates.append((match.group(0), int(round(min(low, high))), int(round(max(low, high)))))
    if not candidates:
        return "", 0, 0
    raw, low, high = max(candidates, key=lambda item: item[2])
    return raw.strip(), low, high


def experience_evidence(text: str) -> tuple[int, str, bool]:
    explicit = list(EXPLICIT_WORK_EXPERIENCE.finditer(text))
    if explicit:
        match = max(explicit, key=lambda value: int(value.group("years") or value.group("years2") or 0))
        years = int(match.group("years") or match.group("years2") or 0)
        return years, match.group(0).strip()[:180], True
    candidates = []
    for match in GENERIC_EXPERIENCE.finditer(text):
        window = text[max(0, match.start() - 55): min(len(text), match.end() + 75)]
        if SKILL_EXPERIENCE.search(window):
            continue
        candidates.append((int(match.group("years") or 0), match.group(0).strip()[:180]))
    if candidates:
        years, evidence = max(candidates, key=lambda pair: pair[0])
        return years, evidence, False
    return 0, "", False


def assess(row: dict, text: str) -> dict:
    role = row.get("role", "")
    source = row.get("source", "")
    combined = " ".join([role, text, row.get("match", ""), row.get("graduation", "")])
    result = {
        "ng_confidence": "Uncertain",
        "ng_evidence": "no explicit new-grad language; retained for review",
        "experience_requirement": "",
        "citizenship_required": row.get("citizenship_required", "Unknown"),
        "sponsorship": row.get("sponsorship", "Unknown"),
        "visa_evidence": row.get("visa_evidence", ""),
        "phd_required": row.get("phd_required", "Unknown"),
        "salary": row.get("salary", "Not listed"),
        "salary_min_annual": row.get("salary_min_annual", ""),
        "salary_max_annual": row.get("salary_max_annual", ""),
        "reject_reason": "",
    }
    if match := CITIZEN.search(combined):
        result["citizenship_required"] = "Yes"
        result["visa_evidence"] = match.group(0)[:180]
    if match := NO_SPONSOR.search(combined):
        result["sponsorship"] = "No"
        result["visa_evidence"] = match.group(0)[:180]
    elif result["sponsorship"] not in {"Yes", "No"} and (match := YES_SPONSOR.search(combined)):
        result["sponsorship"] = "Yes"
        result["visa_evidence"] = match.group(0)[:180]
    if result["salary"] in {"", "Unknown", "Not listed", None}:
        raw, low, high = salary_from_text(text)
        if raw:
            result["salary"] = raw
            result["salary_min_annual"] = low
            result["salary_max_annual"] = high
    result["phd_required"] = "Yes" if PHD_REQUIRED.search(combined) else (
        "No" if result["phd_required"] in {"", "Unknown", None} else result["phd_required"]
    )
    if result["citizenship_required"] == "Yes":
        result["reject_reason"] = "citizenship/security-clearance requirement"
        return result
    if result["sponsorship"] == "No":
        result["reject_reason"] = "no sponsorship"
        return result
    if PURE_HARDWARE.search(role) and not SOFTWARE_RESCUE.search(role):
        result["reject_reason"] = "pure hardware role"
        return result
    if match := OBVIOUS_SENIOR_TITLE.search(role):
        result["ng_confidence"] = "Not NG"
        result["ng_evidence"] = f"senior-level title: {match.group(0)}"
        result["reject_reason"] = result["ng_evidence"]
        return result
    years, evidence, explicit_work = experience_evidence(combined)
    result["experience_requirement"] = evidence
    if years >= 2 and explicit_work:
        result["ng_confidence"] = "Not NG"
        result["ng_evidence"] = f"requires {years}+ years professional/work experience"
        result["reject_reason"] = result["ng_evidence"]
        return result
    if years >= 3:
        result["ng_confidence"] = "Not NG"
        result["ng_evidence"] = f"appears to require {years}+ years prior work experience"
        result["reject_reason"] = result["ng_evidence"]
        return result
    if match := NEW_GRAD.search(role):
        result["ng_confidence"] = "Confirmed"
        result["ng_evidence"] = f"title says {match.group(0)}"
    elif match := NG_BODY.search(text):
        result["ng_confidence"] = "Confirmed"
        result["ng_evidence"] = f"posting says {match.group(0)[:100]}"
    elif TRUSTED_NG_SOURCE.search(source) and YEAR_2027.search(combined):
        result["ng_confidence"] = "Likely"
        result["ng_evidence"] = "new-grad repository plus 2027-cycle evidence"
    elif TRUSTED_NG_SOURCE.search(source):
        result["ng_confidence"] = "Likely"
        result["ng_evidence"] = "listed in a new-grad repository; employer wording is less explicit"
    elif YEAR_2027.search(combined):
        result["ng_confidence"] = "Uncertain"
        result["ng_evidence"] = "2027 reference found, but new-grad eligibility is not explicit"
    maximum = int(float(str(result.get("salary_max_annual") or "0").replace(",", "")))
    if maximum and maximum < 100_000:
        result["reject_reason"] = "maximum stated annual salary below $100k"
    return result


def qualify(row: dict) -> tuple[dict, str]:
    text, posted = fetch_job_text(row.get("url", ""))
    return assess(row, text), posted


def cache_is_fresh(entry: dict) -> bool:
    checked = clean_date(entry.get("checked_at"))
    if not checked:
        return False
    try:
        return date.fromisoformat(checked) >= date.today() - timedelta(days=7)
    except ValueError:
        return False


def integer(value: object) -> int:
    try:
        return int(float(str(value or "0").replace(",", "")))
    except (TypeError, ValueError):
        return 0


def date_key(job: dict) -> str:
    return job.get("posted_date") or job.get("date_added") or "0000-00-00"


def ng_order(job: dict) -> int:
    return {"Confirmed": 0, "Likely": 1, "Uncertain": 2, "Not NG": 3}.get(
        str(job.get("ng_confidence", "Uncertain")), 2
    )


def sort_key(job: dict) -> tuple:
    day = integer(date_key(job).replace("-", ""))
    phd = 1 if str(job.get("phd_required", "")).casefold() == "yes" else 0
    salary = integer(job.get("salary_max_annual"))
    low_pay = 1 if salary and salary < 200_000 else 0
    score = integer(job.get("personalized_score"))
    return (
        0 if job.get("status") == "Open" else 1,
        -day,
        ng_order(job),
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


def load_cache(name: str) -> dict:
    path = DATA / name
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def apply_assessment(row: dict, assessment: dict) -> None:
    for field in [
        "ng_confidence", "ng_evidence", "experience_requirement",
        "citizenship_required", "sponsorship", "visa_evidence", "phd_required",
        "salary", "salary_min_annual", "salary_max_annual",
    ]:
        value = assessment.get(field)
        if value not in {None, ""}:
            row[field] = value


def ng_label(row: dict) -> str:
    return {
        "Confirmed": "✅ Confirmed",
        "Likely": "◐ Likely",
        "Uncertain": "❔ Review",
    }.get(row.get("ng_confidence"), "❔ Review")


def save(rows: list[dict], fields: list[str], date_cache: dict, qualification_cache: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    for field in ["ng_confidence", "ng_evidence", "experience_requirement", "posted_date"]:
        if field not in fields:
            insertion = fields.index("date_added") if "date_added" in fields else len(fields)
            fields.insert(insertion, field)
    with (DATA / "jobs.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    (DATA / "jobs.json").write_text(
        json.dumps([{field: row.get(field, "") for field in fields} for row in rows], indent=2) + "\n",
        encoding="utf-8",
    )
    (DATA / "posted_dates.json").write_text(
        json.dumps(dict(sorted(date_cache.items())), indent=2) + "\n",
        encoding="utf-8",
    )
    (DATA / "job_qualification_cache.json").write_text(
        json.dumps(dict(sorted(qualification_cache.items())), indent=2) + "\n",
        encoding="utf-8",
    )
    open_rows = [row for row in rows if row.get("status") == "Open"]
    parts = [
        "# 2027 North America New Grad Full-Time Jobs", "",
        f"> Last automated update: **{TODAY}** · Open roles: **{len(open_rows)}**", "",
        "Default order is newest ATS posting date (or first-discovery date) first. Within the same date: confirmed/likely new-grad roles first, then non-PhD roles, then better-known compensation; personalized fit is only a later tie-breaker.", "",
        "> Hard filters remove explicit no-sponsorship, U.S.-citizenship/security-clearance, pure hardware, clearly senior titles, clear prior professional-work-experience requirements, and stated salary ranges entirely below $100k.", "",
        "A listing's presence in another new-grad repository is supporting evidence, not proof. Ambiguous roles are retained as **Review** and moved later rather than deleted.", "",
        "| Posted | Company | Role | Salary | Location | Visa | New grad? | PhD |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in open_rows:
        visa = "✅" if row.get("sponsorship") == "Yes" else "❔"
        phd = "PhD only" if row.get("phd_required") == "Yes" else "—"
        role_name = str(row.get("role", "")).replace("|", "/")
        role = f"[{role_name}]({row.get('url', '')})" if row.get("url") else role_name
        values = [
            date_key(row), f"**{str(row.get('company', '')).replace('|', '/')}**", role,
            str(row.get("salary") or "Not listed").replace("|", "/"),
            str(row.get("location", "")).replace("|", "/"), visa, ng_label(row), phd,
        ]
        parts.append("| " + " | ".join(values) + " |")
    parts += [
        "", "## New-grad semantics", "",
        "- **Confirmed:** employer title/description explicitly says new grad, graduate, early career, entry level, or equivalent.",
        "- **Likely:** appears in a trusted new-grad repository without conflicting seniority/work-experience evidence.",
        "- **Review:** only weak evidence such as a 2027 reference; retained but ranked later.",
        "- Years of programming/coding/coursework/research experience are not treated as years of professional employment.",
        "", "## Date semantics", "",
        "- `posted_date`: ATS creation/publication date when available.",
        "- Otherwise it falls back to this repository's first-discovery `date_added`.",
        "- ATS update timestamps are not presented as original publication dates.", "",
        "Listings can close or change without notice. Verify all details before applying.",
    ]
    (ROOT / "README.md").write_text("\n".join(parts) + "\n", encoding="utf-8")


def main() -> None:
    rows, fields = load_rows()
    if not rows:
        print("No job rows to process.")
        return
    qualification_cache = load_cache("job_qualification_cache.json")
    date_cache = load_cache("posted_dates.json")
    pending = {}
    rejected_counts = {}
    for index, row in enumerate(rows):
        url = row.get("url", "")
        cached = qualification_cache.get(url, {}) if url else {}
        if cached and cache_is_fresh(cached):
            apply_assessment(row, cached)
        else:
            pending[index] = row
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(qualify, row): index for index, row in pending.items()}
        for future in as_completed(futures):
            index = futures[future]
            row = rows[index]
            try:
                assessment, posted = future.result()
            except Exception:
                assessment, posted = assess(row, ""), ""
            apply_assessment(row, assessment)
            assessment["checked_at"] = TODAY
            if row.get("url"):
                qualification_cache[row["url"]] = assessment
                if posted:
                    date_cache[row["url"]] = posted
    kept = []
    for row in rows:
        url = row.get("url", "")
        cached = qualification_cache.get(url, {}) if url else {}
        reject_reason = cached.get("reject_reason", "")
        if not row.get("ng_confidence"):
            assessment = assess(row, "")
            apply_assessment(row, assessment)
            reject_reason = reject_reason or assessment.get("reject_reason", "")
        if reject_reason or row.get("ng_confidence") == "Not NG":
            reason = reject_reason or row.get("ng_evidence") or "not new grad"
            rejected_counts[reason] = rejected_counts.get(reason, 0) + 1
            continue
        posted = clean_date(row.get("posted_date")) or clean_date(date_cache.get(url))
        posted = posted or clean_date(row.get("date_added")) or TODAY
        row["posted_date"] = posted
        if url:
            date_cache[url] = posted
        kept.append(row)
    kept.sort(key=sort_key)
    save(kept, fields, date_cache, qualification_cache)
    summary = ", ".join(f"{key}: {value}" for key, value in sorted(rejected_counts.items())) or "none"
    print(f"Kept {len(kept)} jobs; rejected {sum(rejected_counts.values())}. Reasons: {summary}")


if __name__ == "__main__":
    main()
