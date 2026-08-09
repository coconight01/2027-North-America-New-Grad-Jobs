#!/usr/bin/env python3
"""Final hard guard for the North America full-time new-grad dataset.

Discovery feeds and search pages can mislabel or truncate titles.  This pass is
intentionally source-agnostic and checks both visible metadata and the job URL
before the more nuanced new-grad confidence/ranking pass runs.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# These are not ordinary permanent new-grad full-time roles for this tracker.
NON_FTE_PROGRAM = re.compile(
    r"\b(?:student researcher|research student|student worker|visiting researcher|"
    r"intern(?:ship)?|co[- ]?op|apprentice(?:ship)?|fellowship|skillbridge|"
    r"returnship|externship|part[- ]?time|contract(?:or|ing)?)\b",
    re.I,
)

# Special programs limited to transitioning/active-duty military members.
MILITARY_TRANSITION = re.compile(
    r"\b(?:dod[- ]?skillbridge|skillbridge|transitioning (?:military |service )?members?|"
    r"active[- ]?duty (?:military |service )?members?|final \d+[-– ]?\d* months of military service)\b",
    re.I,
)


def reason(row: dict) -> str:
    text = " ".join(
        str(row.get(field, ""))
        for field in ("role", "url", "source", "match", "ng_evidence", "experience_requirement")
    )
    if MILITARY_TRANSITION.search(text):
        return "military-transition/SkillBridge program"
    if NON_FTE_PROGRAM.search(text):
        return "internship/fellowship/other non-FTE program"
    return ""


def main() -> None:
    csv_path = DATA / "jobs.csv"
    json_path = DATA / "jobs.json"
    if not csv_path.exists():
        print("No jobs.csv; nothing to guard.")
        return

    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]

    kept, rejected = [], []
    for row in rows:
        why = reason(row)
        if why:
            rejected.append((row.get("company", ""), row.get("role", ""), row.get("url", ""), why))
        else:
            kept.append(row)

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(kept)

    json_path.write_text(
        json.dumps([{field: row.get(field, "") for field in fields} for row in kept], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Final guard kept {len(kept)} roles and removed {len(rejected)} non-FTE/special-program roles.")
    for company, role, url, why in rejected[:80]:
        print(f"REMOVE [{why}] {company} - {role} - {url}")


if __name__ == "__main__":
    main()
