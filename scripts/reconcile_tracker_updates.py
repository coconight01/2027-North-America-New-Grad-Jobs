#!/usr/bin/env python3
"""Merge reviewed email/application-status updates into the public tracker.

The small YAML overlay is intentionally evidence-only: no Gmail message IDs, private links,
or raw email bodies are written to the public repository.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CONFIG = ROOT / "config" / "tracker_email_updates.yml"
TRACKER = DATA / "application_tracker.json"


def norm(value: object) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).split())


def key(item: dict) -> tuple[str, str]:
    return norm(item.get("company")), norm(item.get("role"))


def main() -> None:
    tracker = json.loads(TRACKER.read_text(encoding="utf-8"))
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    applications = tracker.setdefault("applications", [])
    by_key = {key(item): item for item in applications}
    changed = 0

    for update in config.get("updates", []) or []:
        lookup = (norm(update.get("company")), norm(update.get("role")))
        existing = by_key.get(lookup)
        if existing is None:
            if not update.get("allow_create", False):
                print(f"skip unmatched tracker update: {lookup}")
                continue
            existing = {
                "company": update.get("company", ""),
                "role": update.get("role", ""),
                "status": update.get("status", "Applied"),
                "applied_date": update.get("applied_date"),
                "status_date": update.get("status_date"),
                "confidence": update.get("confidence", "Exact"),
                "evidence": update.get("evidence", "Reviewed application email evidence"),
                "next_action": update.get("next_action", "Wait for review"),
            }
            applications.append(existing)
            by_key[lookup] = existing
            changed += 1

        for field in (
            "status", "applied_date", "status_date", "confidence", "evidence",
            "next_action", "application_limit_note", "eligibility_note",
        ):
            if field in update and existing.get(field) != update.get(field):
                existing[field] = update.get(field)
                changed += 1

    tracker["updated_at"] = date.today().isoformat()
    TRACKER.write_text(json.dumps(tracker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"tracker reconciliation complete: {len(config.get('updates', []) or [])} reviewed updates, {changed} field changes")


if __name__ == "__main__":
    main()
