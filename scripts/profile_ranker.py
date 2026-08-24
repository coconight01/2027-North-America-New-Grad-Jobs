#!/usr/bin/env python3
"""Resume-aware scoring utilities for the personalized job radar."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "config" / "resume_profile.yml"


def load_profile(path: Path = PROFILE_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def norm(value: object) -> str:
    text = str(value or "").casefold().replace("–", "-").replace("—", "-")
    text = re.sub(r"[^a-z0-9+#./-]+", " ", text)
    return " ".join(text.split())


def has_term(text: str, term: str) -> bool:
    hay = norm(text)
    needle = norm(term)
    return bool(needle) and needle in hay


def matched_terms(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if has_term(text, term)]


def number(value: object) -> int:
    try:
        return int(float(str(value or "0").replace(",", "")))
    except (TypeError, ValueError):
        return 0


def official_source(job: dict) -> bool:
    source = str(job.get("source") or "").casefold()
    url = str(job.get("url") or "").casefold()
    if source.startswith("official:") or "manual official" in source:
        return True
    third_party = ("jobright.ai", "linkedin.com", "ziprecruiter", "jobrapido", "simplify.jobs")
    return bool(url) and not any(domain in url for domain in third_party) and not source.startswith("github:")


def track_score(job: dict, body: str, profile: dict) -> tuple[int, list[tuple[int, str, list[str]]]]:
    title = " ".join([str(job.get("role") or ""), str(job.get("category") or "")])
    results = []
    for track in profile.get("tracks", []):
        title_hits = matched_terms(title, track.get("title_terms", []))
        body_hits = matched_terms(body, track.get("body_terms", []))
        if not title_hits and not body_hits:
            continue
        weight = int(track.get("weight", 0))
        raw = min(weight, len(title_hits) * 9 + len(body_hits) * 3 + (4 if title_hits and body_hits else 0))
        results.append((raw, str(track.get("name", "track")), (title_hits + body_hits)[:5]))
    results.sort(reverse=True)
    if not results:
        return 0, []
    best = results[0][0]
    second = round(results[1][0] * 0.35) if len(results) > 1 else 0
    return best + second, results[:2]


def anchor_score(text: str, profile: dict) -> tuple[int, list[tuple[int, str, list[str]]]]:
    hits = []
    total = 0
    for anchor in profile.get("experience_anchors", []):
        terms = matched_terms(text, anchor.get("terms", []))
        if not terms:
            continue
        weight = int(anchor.get("weight", 0))
        contribution = weight + min(3, max(0, len(terms) - 1))
        total += contribution
        hits.append((contribution, str(anchor.get("label", "experience")), terms[:4]))
    hits.sort(reverse=True)
    return min(total, 32), hits[:3]


def signal_delta(title: str, text: str, profile: dict) -> tuple[int, list[str], list[str]]:
    positive, negative = [], []
    delta = 0
    for signal in profile.get("positive_title_signals", []):
        if matched_terms(title, signal.get("terms", [])):
            delta += int(signal.get("weight", 0))
            positive.append(str(signal.get("label", "positive role signal")))
    for signal in profile.get("negative_signals", []):
        if matched_terms(" ".join([title, text]), signal.get("terms", [])):
            delta -= int(signal.get("weight", 0))
            negative.append(str(signal.get("label", "mismatch")))
    return delta, positive[:2], negative[:2]


def skill_score(job: dict, text: str, profile: dict) -> tuple[int, list[str]]:
    combined = " ".join([str(job.get("skills") or ""), text])
    matches, score = [], 0
    for skill, weight in (profile.get("skills") or {}).items():
        if has_term(combined, str(skill)):
            score += int(weight)
            matches.append(str(skill))
    return min(score, 16), matches[:5]


def freshness_days(job: dict) -> int:
    value = str(job.get("posted_date") or job.get("date_added") or "")[:10]
    try:
        return max(0, (date.today() - date.fromisoformat(value)).days)
    except ValueError:
        return 999


def score_job(job: dict, profile: dict, detail_text: str = "") -> dict:
    ranking = profile.get("ranking") or {}
    title = str(job.get("role") or "")
    metadata = " ".join([
        title, str(job.get("category") or ""), str(job.get("skills") or ""),
        str(job.get("personalized_reason") or ""), str(job.get("ng_evidence") or ""),
        str(job.get("match") or ""),
    ])
    combined = " ".join([metadata, detail_text])
    tscore, tracks = track_score(job, combined, profile)
    ascore, anchors = anchor_score(combined, profile)
    signals, positives, negatives = signal_delta(title, combined, profile)
    sscore, skills = skill_score(job, combined, profile)
    score = int(ranking.get("base_score", 32)) + tscore + ascore + signals + sscore

    ng = str(job.get("ng_confidence") or "Uncertain")
    score += {"Confirmed": 6, "Likely": 3, "Uncertain": -5}.get(ng, -5)
    if str(job.get("sponsorship") or "") == "Yes":
        score += 4
    if official_source(job):
        score += 2
    if has_term(combined, "2027"):
        score += 2

    salary = number(job.get("salary_max_annual"))
    if salary >= 200_000:
        score += 8
    elif salary >= 150_000:
        score += 5
    elif salary >= 100_000:
        score += 2

    company = str(job.get("company") or "")
    bonus = profile.get("company_bonus") or {}
    if any(norm(company) == norm(x) for x in bonus.get("companies", [])):
        score += int(bonus.get("frontier_or_infra", 0))
    if any(norm(company) == norm(x) for x in bonus.get("quant_companies", [])):
        score += int(bonus.get("quant", 0))

    phd = str(job.get("phd_required") or "").casefold() == "yes"
    if phd:
        score -= 9 if ascore >= 22 and tscore >= 24 else 22
    if tscore < 12 and ascore < 8 and re.search(r"\bsoftware (?:development )?engineer\b", title, re.I):
        score -= 8

    score = max(0, min(100, round(score)))
    tier = "Top" if score >= 86 else "Strong" if score >= 74 else "Consider" if score >= 58 else "Stretch"
    reasons = []
    if tracks:
        reasons.append("best track: " + tracks[0][1])
    if anchors:
        reasons.append("resume overlap: " + ", ".join(item[1] for item in anchors[:2]))
    if skills:
        reasons.append("skills: " + ", ".join(skills[:4]))
    if positives:
        reasons.append("role shape: " + ", ".join(positives))
    if negatives:
        reasons.append("downrank: " + ", ".join(negatives))
    if ng in {"Confirmed", "Likely"}:
        reasons.append("new-grad: " + ng.lower())
    if phd:
        reasons.append("PhD requirement penalty")
    if salary >= 200_000:
        reasons.append("$200k+ stated ceiling")
    return {
        "score": score,
        "tier": tier,
        "reason": "; ".join(reasons[:6]) or "general early-career software fit",
        "track_score": tscore,
        "anchor_score": ascore,
        "freshness_days": freshness_days(job),
        "negative_signals": negatives,
    }


def smart_sort_key(job: dict) -> tuple:
    days = freshness_days(job)
    bucket = 0 if days <= 3 else 1 if days <= 10 else 2 if days <= 30 else 3
    ng = {"Confirmed": 0, "Likely": 1, "Uncertain": 2}.get(str(job.get("ng_confidence") or "Uncertain"), 2)
    phd = 1 if str(job.get("phd_required") or "").casefold() == "yes" else 0
    return (
        bucket, -number(job.get("personalized_score")), ng, phd, days,
        -number(job.get("salary_max_annual")), norm(job.get("company")), norm(job.get("role")),
    )
