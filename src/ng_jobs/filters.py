from __future__ import annotations

import re
from collections.abc import Iterable

from .models import Job

EXCLUDE_EMPLOYMENT = re.compile(
    r"\b(intern(ship)?|co-?op|apprentice|part[- ]?time|contract)\b", re.I
)
NEW_GRAD = re.compile(
    r"\b(new grad(uate)?|university grad(uate)?|college grad|graduate "
    r"(software|quantitative|machine learning|data|hardware|engineer|trader|researcher)|"
    r"entry[- ]level|early career|associate software|software engineer i\b|"
    r"engineer i\b|2027 new grads?)\b",
    re.I,
)
EXPLICIT_2027 = re.compile(
    r"\b(2027|december 2026|dec 2026|spring 2027|summer 2027|"
    r"fall 2026|mid-2027|2026[-–/]2027)\b",
    re.I,
)
CONTRADICTORY_YEAR = re.compile(r"\b(2024|2025)\b", re.I)

NORTH_AMERICA = re.compile(
    r"\b(united states|u\.s\.|usa|canada|remote(?:,? (?:us|usa|canada|north america))?|"
    r"alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|"
    r"georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|"
    r"maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|"
    r"nevada|new hampshire|new jersey|new mexico|new york|north carolina|north dakota|"
    r"ohio|oklahoma|oregon|pennsylvania|rhode island|south carolina|south dakota|"
    r"tennessee|texas|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming|"
    r"ontario|quebec|british columbia|alberta|manitoba|saskatchewan|nova scotia|"
    r"new brunswick|newfoundland|prince edward island|yukon|northwest territories|"
    r"chicago|austin|san francisco|new york|seattle|boston|toronto|vancouver|montreal|"
    r"waterloo|palo alto|mountain view|sunnyvale|san jose|los angeles|miami|atlanta|"
    r"denver|dallas|houston|bellevue|redmond|pittsburgh)\b",
    re.I,
)

CATEGORY_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("Quantitative Finance", re.compile(r"\b(quant|trader|trading|investment|equities)\b", re.I)),
    ("AI / Machine Learning", re.compile(r"\b(machine learning|artificial intelligence|ai engineer|data scientist|nlp|llm)\b", re.I)),
    ("Data Engineering", re.compile(r"\b(data engineer|analytics engineer|business intelligence)\b", re.I)),
    ("Infrastructure / Systems", re.compile(r"\b(infrastructure|systems|platform|sre|site reliability|distributed|compiler|database|network)\b", re.I)),
    ("Hardware Engineering", re.compile(r"\b(hardware|firmware|fpga|asic|silicon|embedded|verification)\b", re.I)),
    ("Product Management", re.compile(r"\b(product manager|apm|product management)\b", re.I)),
    ("Software Engineering", re.compile(r"\b(software|developer|full[- ]?stack|backend|frontend|forward deployed)\b", re.I)),
]


def classify(text: str) -> str:
    for category, pattern in CATEGORY_RULES:
        if pattern.search(text):
            return category
    return "Other"


def infer_country(location: str) -> str:
    loc = location.casefold()
    canada = (
        "canada", "ontario", "quebec", "british columbia", "alberta", "toronto",
        "vancouver", "montreal", "waterloo", "ottawa", "calgary"
    )
    if any(x in loc for x in canada):
        return "Canada"
    if NORTH_AMERICA.search(location):
        return "United States"
    return "Unknown"


def is_eligible(job: Job, include_general: bool = False) -> bool:
    text = " ".join([job.role, job.description, job.graduation, job.start_date])
    if EXCLUDE_EMPLOYMENT.search(job.role):
        return False
    if CONTRADICTORY_YEAR.search(text) and not EXPLICIT_2027.search(text):
        return False
    if not NORTH_AMERICA.search(" ".join([job.location, job.country])):
        return False
    if EXPLICIT_2027.search(text):
        job.match = "Explicit 2027"
        return True
    if include_general and NEW_GRAD.search(text):
        job.match = "General new grad"
        return True
    return False


def normalize(job: Job) -> Job:
    combined = f"{job.role} {job.description}"
    job.category = job.category if job.category not in ("", "Other") else classify(combined)
    if job.country in ("", "Unknown"):
        job.country = infer_country(job.location)
    if EXPLICIT_2027.search(" ".join([job.role, job.description, job.graduation, job.start_date])):
        job.match = "Explicit 2027"
        if job.graduation == "Unknown":
            job.graduation = "Includes 2027 graduates"
    return job


def dedupe(jobs: Iterable[Job]) -> list[Job]:
    by_url: dict[str, Job] = {}
    by_fallback: dict[tuple[str, str, str], Job] = {}
    for job in jobs:
        job = normalize(job)
        fallback = (
            re.sub(r"\W+", "", job.company.casefold()),
            re.sub(r"\W+", "", job.role.casefold()),
            re.sub(r"\W+", "", job.location.casefold()),
        )
        if job.url and job.url in by_url:
            existing = by_url[job.url]
            if len(job.description) > len(existing.description):
                by_url[job.url] = job
            continue
        if fallback in by_fallback:
            existing = by_fallback[fallback]
            if job.url and not existing.url:
                by_fallback[fallback] = job
            continue
        if job.url:
            by_url[job.url] = job
        by_fallback[fallback] = job

    unique: dict[str, Job] = {}
    for job in list(by_url.values()) + list(by_fallback.values()):
        key = job.url or job.identity
        unique[key] = job
    return sorted(
        unique.values(),
        key=lambda j: (j.status != "Open", j.date_added, j.company.casefold(), j.role.casefold()),
        reverse=False,
    )
