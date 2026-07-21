#!/usr/bin/env python3
"""Validate that collected postings are actually suitable for new graduates.

Clear senior titles and explicit professional-work-experience requirements are
removed. Ambiguous roles are retained but labelled Uncertain so the date-first
sort can place them later. Programming/coding experience alone is not treated
as professional work experience.
"""
from __future__ import annotations

import csv
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TIMEOUT = 20
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; 2027-New-Grad-Jobs/0.6; "
        "+https://github.com/coconight01/2027-North-America-New-Grad-Jobs)"
    )
}

CLEAR_SENIOR_TITLE = re.compile(
    r"\b(?:principal|director|staff|senior|sr\.?|lead|manager|architect|"
    r"head|vice president|vp|chief|distinguished|fellow|advisor)\b",
    re.I,
)
LEVEL_TWO_TITLE = re.compile(r"\b(?:engineer|developer|scientist|researcher)\s+(?:ii|2)\b", re.I)
EXPLICIT_NEW_GRAD = re.compile(
    r"\b(?:new grad(?:uate)?|new college grad(?:uate)?|university grad(?:uate)?|"
    r"college grad(?:uate)?|early career|entry[- ]level|campus hire|university hire|"
    r"students? and graduates?|class of 2027|2027 (?:start|graduate)|"
    r"graduate (?:software|machine learning|ml|quantitative|data|systems?|engineer|"
    r"developer|researcher|trader))\b",
    re.I,
)
TRUSTED_NG_SOURCE = re.compile(
    r"(?:new[-_ ]?grad|newgrad|2027[-_/].*college[-_ ]jobs|"
    r"2027[-_ ]?(?:swe|ai)[-_ ]college[-_ ]jobs|internships-and-newgrad)",
    re.I,
)
YEAR_MISMATCH_TITLE = re.compile(r"\b2026\b", re.I)
YEAR_2027 = re.compile(r"\b2027\b", re.I)

# These phrases explicitly refer to employment rather than coding practice,
# coursework, research, open-source work, or personal projects.
PROFESSIONAL_EXPERIENCE = re.compile(
    r"(?P<n>\d+)\s*\+?\s*years?\s+(?:of\s+)?(?:non[- ]?internship\s+)?"
    r"(?:professional|industry|full[- ]?time|relevant work|work)\b.{0,90}?experience|"
    r"(?P<n2>\d+)\s*\+?\s*years?\s+of\s+non[- ]?internship\s+professional\s+"
    r"(?:software\s+development\s+)?experience|"
    r"minimum\s+of\s+(?P<n3>\d+)\s+years?\s+(?:of\s+)?(?:professional|industry|work)\s+experience",
    re.I,
)
GENERIC_EXPERIENCE = re.compile(
    r"(?P<n>\d+)\s*\+?\s*years?\s+(?:of\s+)?(?:relevant\s+)?experience",
    re.I,
)
PROGRAMMING_CONTEXT = re.compile(
    r"\b(?:programming|coding|coursework|academic|research|open[- ]source|personal projects?)\b",
    re.I,
)

RANGE_SALARY = re.compile(
    r"(?:\$|USD\s*)\s*(\d{2,3}(?:,\d{3})?)\s*(?:/\s*(?:year|yr))?\s*"
    r"(?:-|–|—|to|through|up to)\s*(?:\$|USD\s*)?\s*"
    r"(\d{2,3}(?:,\d{3})?)(?:\s*/\s*(?:year|yr)|\s+per\s+year|\s+annually)?",
    re.I,
)
HOURLY_RANGE = re.compile(
    r"(?:\$|USD\s*)\s*(\d{2,3}(?:\.\d+)?)\s*(?:-|–|—|to)\s*"
    r"(?:\$|USD\s*)?\s*(\d{2,3}(?:\.\d+)?)\s*(?:/\s*(?:hour|hr)|per\s+hour|hourly)",
    re.I,
)
SINGLE_ANNUAL = re.compile(
    r"(?:\$|USD\s*)\s*(\d{2,3}(?:,\d{3})?)\s*(?:/\s*(?:year|yr)|per\s+year|annually)",
    re.I,
)


def clean_text(value: str) -> str:
    return " ".join(BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True).split())


def get_json(url: str) -> dict:
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def lever_text(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.netloc not in {"jobs.lever.co", "jobs.eu.lever.co"}:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return ""
    host = "api.eu.lever.co" if parsed.netloc == "jobs.eu.lever.co" else "api.lever.co"
    data = get_json(f"https://{host}/v0/postings/{parts[0]}/{parts[1]}?mode=json")
    values = [
        data.get("descriptionPlain"), data.get("description"),
        data.get("additionalPlain"), data.get("additional"),
        data.get("requirementsPlain"),
    ]
    for group in data.get("lists") or []:
        values.extend([group.get("text"), group.get("content")])
    return clean_text(" ".join(str(value or "") for value in values))


def posting_text(url: str) -> str:
    if not url:
        return ""
    try:
        text = lever_text(url)
        if text:
            return text
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if response.status_code >= 400:
            return ""
        return clean_text(response.text)[:120000]
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return ""


def years_from_match(match: re.Match) -> int:
    for name in ("n", "n2", "n3"):
        value = match.groupdict().get(name)
        if value:
            return int(value)
    return 0


def professional_requirement(text: str) -> tuple[int, str]:
    match = PROFESSIONAL_EXPERIENCE.search(text)
    if match:
        return years_from_match(match), match.group(0)[:220]

    # Generic statements are treated conservatively: three or more years and
    # no nearby programming/coding context is clear non-new-grad evidence.
    for match in GENERIC_EXPERIENCE.finditer(text):
        years = int(match.group("n"))
        window = text[max(0, match.start() - 80): match.end() + 100]
        if years >= 3 and not PROGRAMMING_CONTEXT.search(window):
            return years, match.group(0)[:220]
    return 0, ""


def ambiguous_experience(text: str) -> str:
    for match in GENERIC_EXPERIENCE.finditer(text):
        years = int(match.group("n"))
        window = text[max(0, match.start() - 80): match.end() + 100]
        if 1 <= years <= 2 and not PROGRAMMING_CONTEXT.search(window):
            return match.group(0)[:220]
    return ""


def salary_from_text(text: str) -> tuple[str, int, int]:
    candidates: list[tuple[int, int]] = []
    for match in RANGE_SALARY.finditer(text):
        low = int(match.group(1).replace(",", ""))
        high = int(match.group(2).replace(",", ""))
        if 50_000 <= low <= 1_000_000 and 50_000 <= high <= 1_000_000:
            candidates.append((min(low, high), max(low, high)))
    for match in HOURLY_RANGE.finditer(text):
        low = round(float(match.group(1)) * 2080)
        high = round(float(match.group(2)) * 2080)
        if 50_000 <= low <= 1_000_000 and 50_000 <= high <= 1_000_000:
            candidates.append((min(low, high), max(low, high)))

    if not candidates:
        annual_values = []
        for match in SINGLE_ANNUAL.finditer(text):
            value = int(match.group(1).replace(",", ""))
            window = text[max(0, match.start() - 140): match.end() + 140]
            if 50_000 <= value <= 1_000_000 and re.search(
                r"\b(?:salary|base pay|pay range|compensation|annual pay)\b", window, re.I
            ):
                annual_values.append(value)
        if len(annual_values) >= 2:
            candidates.append((min(annual_values), max(annual_values)))

    if not candidates:
        return "", 0, 0
    low, high = max(candidates, key=lambda item: item[1])
    return f"${low:,}–${high:,}/year", low, high


def classify(row: dict, text: str) -> tuple[str, str, str]:
    role = row.get("role", "")
    source = row.get("source", "")
    combined = f"{role} {text}"

    if CLEAR_SENIOR_TITLE.search(role):
        return "Reject", f"Clearly senior title: {CLEAR_SENIOR_TITLE.search(role).group(0)}", "Senior title"

    years, evidence = professional_requirement(text)
    if years:
        return "Reject", evidence, f"Requires {years}+ years professional work experience"

    if YEAR_MISMATCH_TITLE.search(role) and not YEAR_2027.search(combined):
        return "Reject", "Role title targets 2026 rather than 2027", "Wrong graduation/start year"

    explicit = EXPLICIT_NEW_GRAD.search(combined)
    if explicit:
        return "Confirmed", explicit.group(0)[:180], ""

    ambiguous = ambiguous_experience(text)
    if ambiguous:
        return "Uncertain", f"Ambiguous experience requirement: {ambiguous}", ""

    if TRUSTED_NG_SOURCE.search(source):
        evidence = source.split(":", 1)[-1]
        if LEVEL_TWO_TITLE.search(role):
            return "Uncertain", f"New-grad source, but level-II title: {evidence}", ""
        return "Likely", f"Listed by new-grad source: {evidence}", ""

    if YEAR_2027.search(combined):
        return "Uncertain", "2027 appears, but the posting lacks clear new-grad language", ""
    return "Uncertain", "No explicit new-grad requirement found", ""


def review(row: dict) -> tuple[dict, str]:
    text = posting_text(row.get("url", ""))
    confidence, evidence, rejection = classify(row, text)
    row["new_grad_confidence"] = confidence
    row["new_grad_evidence"] = evidence
    row["experience_requirement"] = rejection or ""

    if row.get("salary") in {"", "Unknown", "Not listed", None} and text:
        salary, low, high = salary_from_text(text)
        if salary:
            row["salary"] = salary
            row["salary_min_annual"] = low
            row["salary_max_annual"] = high
    return row, rejection


def load_rows() -> tuple[list[dict], list[str]]:
    path = DATA / "jobs.csv"
    if not path.exists():
        return [], []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader], list(reader.fieldnames or [])


def save(rows: list[dict], fields: list[str], counts: dict[str, int]) -> None:
    for field in ("new_grad_confidence", "new_grad_evidence", "experience_requirement"):
        if field not in fields:
            fields.append(field)
    with (DATA / "jobs.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    (DATA / "jobs.json").write_text(
        json.dumps([{field: row.get(field, "") for field in fields} for row in rows], indent=2)
        + "\n",
        encoding="utf-8",
    )
    (DATA / "qualification_status.json").write_text(
        json.dumps({"kept": len(rows), "removed": counts}, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    rows, fields = load_rows()
    if not rows:
        print("No jobs to qualify.")
        return

    reviewed: list[dict | None] = [None] * len(rows)
    rejected: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(review, row): index for index, row in enumerate(rows)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                row, reason = future.result()
            except Exception as exc:  # Keep uncertain rather than losing a role.
                row = rows[index]
                row["new_grad_confidence"] = "Uncertain"
                row["new_grad_evidence"] = f"Qualification check failed: {exc}"
                row["experience_requirement"] = ""
                reason = ""
            if reason:
                rejected[reason] = rejected.get(reason, 0) + 1
            else:
                reviewed[index] = row

    kept = [row for row in reviewed if row is not None]
    save(kept, fields, rejected)
    print(f"Qualified {len(kept)} jobs; removed {len(rows) - len(kept)} clearly non-new-grad roles.")
    for reason, count in sorted(rejected.items()):
        print(f"  {reason}: {count}")


if __name__ == "__main__":
    main()
