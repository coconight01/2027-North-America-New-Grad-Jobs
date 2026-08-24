#!/usr/bin/env python3
"""Compatibility runner for the legacy collector monolith."""
from __future__ import annotations

import re
import sys


def patch_update_jobs():
    import update_jobs
    if not hasattr(update_jobs, "EXPLICIT_2026_ROLE"):
        update_jobs.EXPLICIT_2026_ROLE = re.compile(
            r"\b(?:2026|class of 2026|new college grad(?:uate)? 2026|new grad(?:uate)? 2026)\b", re.I
        )
    if not hasattr(update_jobs, "UNVERIFIED_SOURCE_CYCLE"):
        update_jobs.UNVERIFIED_SOURCE_CYCLE = re.compile(
            r"\b2027 source cycle\s*\(unverified\)\b", re.I
        )
    return update_jobs


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in {"update", "extra"}:
        raise SystemExit("usage: pipeline_runner.py {update|extra} [args...]")
    mode, args = sys.argv[1], sys.argv[2:]
    update_jobs = patch_update_jobs()
    if mode == "update":
        sys.argv = ["update_jobs.py", *args]
        update_jobs.main()
        return
    import extra_discovery
    sys.argv = ["extra_discovery.py", *args]
    extra_discovery.main()


if __name__ == "__main__":
    main()
