#!/usr/bin/env python3
"""Fast runtime checks with persisted diagnostics for GitHub Actions failures."""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def base_job(role: str, category: str = "Software Engineering") -> dict:
    return {
        "company": "Example",
        "role": role,
        "category": category,
        "location": "San Francisco, CA",
        "country": "United States",
        "graduation": "2027 source cycle (unverified)",
        "start_date": "Unknown",
        "sponsorship": "Unknown",
        "citizenship_required": "Unknown",
        "phd_required": "No",
        "salary_max_annual": 180000,
        "skills": "Python, C++, Linux, Distributed Systems",
        "source": "Official:Example",
        "status": "Open",
        "ng_confidence": "Likely",
        "ng_evidence": "entry-level title",
        "match": "Third-party 2027 discovery source; eligibility unverified",
        "date_added": "2026-08-24",
        "posted_date": "2026-08-24",
        "url": "https://example.com/job",
    }


def test_legacy_runtime() -> str:
    from pipeline_runner import patch_update_jobs
    update_jobs = patch_update_jobs()
    row = base_job("Software Engineer, Early Career")
    assert update_jobs.eligible(dict(row), True) is True
    return "legacy eligible() runtime ok"


def test_package_filters() -> str:
    from src.ng_jobs import filters as package_filters
    package_2026 = SimpleNamespace(
        role="Software Engineer - New Grad 2026", description="", graduation="Unknown",
        start_date="", location="Seattle, WA", country="United States", match="",
    )
    assert package_filters.is_eligible(package_2026, True) is False
    package_internships = SimpleNamespace(
        role="NVIDIA 2027 Internships: Developer and Performance Technology",
        description="", graduation="2027", start_date="", location="Santa Clara, CA",
        country="United States", match="",
    )
    assert package_filters.is_eligible(package_internships, True) is False
    package_clearance = SimpleNamespace(
        role="Mission Software Infrastructure Engineer, Active Clearance",
        description="", graduation="2027", start_date="",
        location="Costa Mesa, CA", country="United States", match="",
    )
    assert package_filters.is_eligible(package_clearance, True) is False
    package_unverified = SimpleNamespace(
        role="Software Engineer, Early Career", description="",
        graduation="2027 source cycle (unverified)", start_date="",
        location="Seattle, WA", country="United States", match="",
    )
    assert package_filters.is_eligible(package_unverified, True) is True
    return "package filters runtime ok, including plural internships"


def test_ranking() -> str:
    from profile_ranker import load_profile, score_job
    profile = load_profile()
    ml = base_job("Machine Learning Systems Engineer, New Grad", "AI / Machine Learning")
    backend = base_job("Software Engineer I, Backend")
    frontend = base_job("Frontend Software Engineer I")
    ml_text = "Build distributed training and LLM inference systems using NCCL, CUDA, vLLM, profiling and multi-GPU parallelism."
    backend_text = "Build scalable backend services in Python and Linux."
    frontend_text = "Build React web UI and frontend applications."
    ml_score = score_job(ml, profile, ml_text)["score"]
    backend_score = score_job(backend, profile, backend_text)["score"]
    frontend_score = score_job(frontend, profile, frontend_text)["score"]
    assert ml_score > backend_score > frontend_score, (ml_score, backend_score, frontend_score)
    assert ml_score >= 80, ml_score
    return f"ranking ok: ml={ml_score}, backend={backend_score}, frontend={frontend_score}"


def test_title_noise_and_vetoes() -> str:
    from profile_ranker import load_profile, score_job
    from smart_rank_jobs import hard_veto
    profile = load_profile()
    infra = base_job("Software Engineer Graduate - AI Search Infra Team - 2027 Start")
    noisy_page = "Build inference infrastructure with CUDA and distributed systems. TikTok is a mobile video product with frontend experiences."
    result = score_job(infra, profile, noisy_page)
    assert "frontend-heavy" not in result["negative_signals"], result
    assert "mobile specialization" not in result["negative_signals"], result
    assert hard_veto(base_job("NVIDIA 2027 Internships: Developer and Performance Technology"))
    assert hard_veto(base_job("Research Scientist - R&D - 2026"))
    assert hard_veto(base_job("Infrastructure Engineer, Active Clearance"))
    assert not hard_veto(base_job("Machine Learning Engineer Graduate - 2027 Start"))
    return f"title-noise/vetoes ok: infra={result['score']}"


def test_application_dedupe() -> str:
    from profile_ranker import load_profile
    from smart_rank_jobs import build_review_queue
    profile = load_profile()

    held = base_job("Machine Learning Engineer Graduate (AML Engine) - 2027 Start", "AI / Machine Learning")
    held["company"] = "TikTok USDS Joint Venture"
    held["personalized_score"] = 100
    held["priority"] = "Top"
    hold_tracker = {
        "applications": [{
            "company": "ByteDance",
            "role": "Research Scientist Graduates - 2027 Start",
            "status": "Applied",
            "confidence": "Exact",
            "next_action": "Keep all additional ByteDance/TikTok roles on hold",
        }]
    }
    assert build_review_queue([held], profile, hold_tracker) == []

    exact = base_job("Software Engineer, Early Career")
    exact["company"] = "ExampleCo"
    exact["personalized_score"] = 100
    exact_tracker = {
        "applications": [{
            "company": "ExampleCo",
            "role": "Software Engineer, Early Career",
            "status": "Applied",
            "confidence": "User-confirmed exact role",
        }]
    }
    assert build_review_queue([exact], profile, exact_tracker) == []

    requisition_variant = base_job("Software Engineer - New Grad - 2027")
    requisition_variant.update({
        "company": "ExampleCo",
        "url": "https://boards.example/jobs/5211582007",
        "personalized_score": 100,
    })
    requisition_tracker = {
        "applications": [{
            "company": "ExampleCo",
            "role": "Software Engineer, New Grad (2027) (5211582007)",
            "status": "Applied",
            "confidence": "User-confirmed exact role",
        }]
    }
    assert build_review_queue([requisition_variant], profile, requisition_tracker) == []

    possible = base_job("Machine Learning Infrastructure Engineer")
    possible["company"] = "MaybeCo"
    possible["personalized_score"] = 100
    possible_tracker = {
        "applications": [{
            "company": "MaybeCo",
            "role": "Role uncertain",
            "status": "Applied",
            "confidence": "Company-only",
        }]
    }
    queue = build_review_queue([possible], profile, possible_tracker)
    assert len(queue) == 1, queue
    assert queue[0]["application_match"] == "Company-only possible", queue[0]

    duplicate_a = base_job("Software Development Engineer I, Annapurna Labs, Early Career - 2027")
    duplicate_a.update({"company": "Amazon", "personalized_score": 99})
    duplicate_b = dict(duplicate_a)
    duplicate_b.update({
        "company": "Annapurna Labs (U.S.) Inc. - D63",
        "location": "Cupertino, CA",
        "url": "https://aggregator.example/duplicate",
        "personalized_score": 98,
    })
    assert len(build_review_queue([duplicate_a, duplicate_b], profile, {})) == 1
    return "application and semantic-role dedupe ok, including ByteDance/TikTok and Amazon/Annapurna aliases"


def test_manual_official_status_override() -> str:
    from smart_rank_jobs import hard_veto

    stale = base_job("Compiler Engineer, Agentic Compilation Systems - New College Grad 2027 (JR2026218)")
    stale.update({
        "company": "NVIDIA",
        "url": "https://cached.example/jobs/JR2026218",
    })
    assert hard_veto(stale) == "manual official-status override: closed/unavailable"
    adjacent = dict(stale)
    adjacent.update({"role": "AI Compiler Engineer - New College Grad 2027 (JR2026011)", "url": "https://example.com/JR2026011"})
    assert not hard_veto(adjacent)
    return "exact closed-requisition override blocks stale discovery copies without suppressing adjacent roles"


def test_pure_hardware_veto() -> str:
    from smart_rank_jobs import hard_veto

    hardware = base_job("Cloud Hardware Development Engineer I, Early Career - 2027", "Hardware Engineering")
    assert hard_veto(hardware) == "pure hardware role"
    runtime = base_job("GPU Runtime Software Engineer, New Grad", "Hardware Engineering")
    assert not hard_veto(runtime)
    return "pure hardware is vetoed while GPU runtime/software roles remain eligible"


def test_jd_excerpt_stays_with_job_after_sort() -> str:
    from smart_rank_jobs import index_detail_text_by_url

    rows = [
        {"company": "Alpha", "role": "First", "url": "https://example.com/alpha"},
        {"company": "Beta", "role": "Second", "url": "https://example.com/beta"},
    ]
    detail_text = {0: "alpha-specific JD", 1: "beta-specific JD"}
    by_url = index_detail_text_by_url(rows, detail_text)

    rows.reverse()
    assert by_url[rows[0]["url"]] == "beta-specific JD", by_url
    assert by_url[rows[1]["url"]] == "alpha-specific JD", by_url
    return "JD excerpts remain URL-associated after row sorting"


def test_github_markdown_link_title_cleanup() -> str:
    import update_jobs

    class Response:
        text = "| **Sierra** | [Software Engineer, Agent (New Grad 2027)](https://jobs.example.com/agent) | San Francisco, CA | Sep 1 |"

        @staticmethod
        def raise_for_status() -> None:
            return None

    original_get = update_jobs.requests.get
    update_jobs.requests.get = lambda *args, **kwargs: Response()
    try:
        rows = update_jobs.github_readme({"repo": "example/jobs", "path": "README.md"})
    finally:
        update_jobs.requests.get = original_get
    assert len(rows) == 1, rows
    assert rows[0]["role"] == "Software Engineer, Agent (New Grad 2027)", rows[0]
    assert rows[0]["url"] == "https://jobs.example.com/agent", rows[0]
    return "GitHub Markdown-linked titles normalize without leaking URL syntax"


def test_priority_company_detail_recall() -> str:
    from profile_ranker import load_profile
    from smart_rank_jobs import company_priority, select_detail_indexes
    profile = load_profile()
    rows = []
    for i in range(5):
        row = base_job(f"Machine Learning Systems Engineer, New Grad {i}", "AI / Machine Learning")
        row["company"] = f"HighScore{i}"
        row["url"] = f"https://example.com/high-{i}"
        rows.append(row)
    waymo = base_job("Software Engineer, Simulation Tools", "Other")
    waymo.update({
        "company": "Waymo",
        "skills": "",
        "salary_max_annual": "",
        "date_added": "2026-07-01",
        "posted_date": "2026-07-01",
        "url": "https://careers.withwaymo.com/jobs/example",
    })
    rows.append(waymo)
    assert company_priority("Waymo", profile) == "Tier A"
    indexes, metadata = select_detail_indexes(rows, profile, 3)
    assert 5 in indexes, (indexes, metadata)
    assert metadata["actual_unique_selections"]["priority_companies"] >= 1, metadata
    return f"priority-company detail recall ok: selected={indexes}"


def test_china_detail_fallback() -> str:
    from scripts.china_discovery import needs_detail

    assert needs_detail({"title": "AI Infrastructure Engineer"}) is True
    assert needs_detail({"locations": []}) is True
    assert needs_detail({"locations": [{"cityName": "深圳市"}]}) is False
    return "China Moka rows missing concrete locations trigger exact-job detail lookup"


def test_tracker_overlay_coverage() -> str:
    from scripts import reconcile_tracker_updates

    assert "tracker_email_updates*.yml" in reconcile_tracker_updates.CONFIG_GLOBS
    assert "tracker_updates*.yml" in reconcile_tracker_updates.CONFIG_GLOBS
    paths, updates = reconcile_tracker_updates.load_updates()
    assert any(path.name.startswith("tracker_updates_") for path in paths), paths
    assert any(update.get("remove") for update in updates), "expected a reviewed removal overlay"
    tracker = json.loads((DATA / "application_tracker.json").read_text(encoding="utf-8"))
    assert not any(
        item.get("company") == "DRW" and item.get("role") == "Role uncertain"
        for item in tracker.get("applications", [])
    )
    return "email and user-confirmed tracker overlays load together; reviewed removals persist"


def main() -> None:
    tests = [
        ("legacy_runtime", test_legacy_runtime),
        ("package_filters", test_package_filters),
        ("ranking", test_ranking),
        ("title_noise_and_vetoes", test_title_noise_and_vetoes),
        ("application_dedupe", test_application_dedupe),
        ("manual_official_status_override", test_manual_official_status_override),
        ("pure_hardware_veto", test_pure_hardware_veto),
        ("jd_excerpt_association", test_jd_excerpt_stays_with_job_after_sort),
        ("github_markdown_title_cleanup", test_github_markdown_link_title_cleanup),
        ("priority_company_detail_recall", test_priority_company_detail_recall),
        ("china_detail_fallback", test_china_detail_fallback),
        ("tracker_overlay_coverage", test_tracker_overlay_coverage),
    ]
    report = {"overall": "success", "tests": {}}
    for name, fn in tests:
        try:
            detail = fn()
            report["tests"][name] = {"outcome": "success", "detail": detail}
        except Exception as exc:
            report["overall"] = "failure"
            report["tests"][name] = {
                "outcome": "failure",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(limit=8),
            }
    DATA.mkdir(exist_ok=True)
    (DATA / "smoke_status.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report["overall"] != "success":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
