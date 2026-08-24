#!/usr/bin/env python3
"""Fast runtime checks that catch NameError and ranking regressions missed by py_compile."""
from __future__ import annotations

from types import SimpleNamespace

from pipeline_runner import patch_update_jobs
from profile_ranker import load_profile, score_job
from smart_rank_jobs import build_review_queue, hard_veto
from src.ng_jobs import filters as package_filters


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
    }


def main() -> None:
    update_jobs = patch_update_jobs()
    row = base_job("Software Engineer, Early Career")
    assert update_jobs.eligible(dict(row), True) is True

    # Exercise the package implementation too; py_compile alone would miss undefined globals.
    package_2026 = SimpleNamespace(
        role="Software Engineer - New Grad 2026", description="", graduation="Unknown",
        start_date="", location="Seattle, WA", country="United States", match="",
    )
    assert package_filters.is_eligible(package_2026, True) is False
    package_unverified = SimpleNamespace(
        role="Software Engineer, Early Career", description="",
        graduation="2027 source cycle (unverified)", start_date="",
        location="Seattle, WA", country="United States", match="",
    )
    assert package_filters.is_eligible(package_unverified, True) is True

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

    infra = base_job("Software Engineer Graduate - AI Search Infra Team - 2027 Start")
    noisy_page = "Build inference infrastructure with CUDA and distributed systems. TikTok is a mobile video product with frontend experiences."
    infra_result = score_job(infra, profile, noisy_page)
    assert "frontend-heavy" not in infra_result["negative_signals"], infra_result
    assert "mobile specialization" not in infra_result["negative_signals"], infra_result

    assert hard_veto(base_job("NVIDIA 2027 Internships: Developer and Performance Technology"))
    assert hard_veto(base_job("Research Scientist - R&D - 2026"))
    assert not hard_veto(base_job("Machine Learning Engineer Graduate - 2027 Start"))

    held = base_job("Machine Learning Engineer Graduate (AML Engine) - 2027 Start", "AI / Machine Learning")
    held["company"] = "TikTok"
    held["personalized_score"] = 100
    held["priority"] = "Top"
    tracker = {
        "applications": [{
            "company": "ByteDance",
            "role": "Research Scientist Graduates - 2027 Start",
            "status": "Applied",
            "confidence": "Exact",
            "next_action": "Keep all additional ByteDance/TikTok roles on hold",
        }]
    }
    assert build_review_queue([held], profile, tracker) == []

    # Exact role evidence suppresses the duplicate role.
    exact = base_job("Software Engineer, Early Career")
    exact["company"] = "ExampleCo"
    exact["personalized_score"] = 100
    exact_tracker = {
        "applications": [{
            "company": "ExampleCo",
            "role": "Software Engineer, Early Career",
            "status": "Applied",
            "confidence": "Exact",
        }]
    }
    assert build_review_queue([exact], profile, exact_tracker) == []

    # Company-only evidence must warn, not hide every role at that company.
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
    possible_queue = build_review_queue([possible], profile, possible_tracker)
    assert len(possible_queue) == 1
    assert possible_queue[0]["application_match"] == "Company-only possible"

    print(
        f"smoke ok: ml={ml_score}, backend={backend_score}, frontend={frontend_score}, "
        f"infra={infra_result['score']}, application-dedupe=ok"
    )


if __name__ == "__main__":
    main()
