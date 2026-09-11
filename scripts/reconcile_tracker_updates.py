#!/usr/bin/env python3
"""Merge reviewed email/application-status updates into the public tracker.

Reviewed observations live in one or more small YAML overlays named
``tracker_email_updates*.yml``. Keeping daily evidence in separate overlays
avoids rewriting a growing history file and makes exact-requisition updates
easy to audit. No Gmail IDs, private links, or raw email bodies are written
to the public repository.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CONFIG_DIR = ROOT / "config"
CONFIG_GLOB = "tracker_email_updates*.yml"
TRACKER = DATA / "application_tracker.json"


def norm(value: object) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).split())


def key(item: dict) -> tuple[str, str]:
    return norm(item.get("company")), norm(item.get("role"))


def can_create(update: dict) -> bool:
    """Exact reviewed email evidence may create a new tracker row by default."""
    return bool(update.get("allow_create", False)) or norm(update.get("confidence")) == "exact"


def load_updates() -> tuple[list[Path], list[dict]]:
    paths = sorted(CONFIG_DIR.glob(CONFIG_GLOB))
    updates: list[dict] = []
    for path in paths:
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        batch = config.get("updates", []) or []
        if not isinstance(batch, list):
            raise ValueError(f"{path}: updates must be a list")
        updates.extend(batch)
    return paths, updates


def main() -> None:
    tracker = json.loads(TRACKER.read_text(encoding="utf-8"))
    config_paths, updates = load_updates()
    applications = tracker.setdefault("applications", [])
    by_key = {key(item): item for item in applications}
    changed = 0

    for update in updates:
        lookup = (norm(update.get("company")), norm(update.get("role")))
        existing = by_key.get(lookup)
        if existing is None:
            if not all(lookup) or not can_create(update):
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
    print(
        f"tracker reconciliation complete: {len(updates)} reviewed updates "
        f"from {len(config_paths)} overlay files, {changed} field changes"
    )


if __name__ == "__main__":
    main()
