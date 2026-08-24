#!/usr/bin/env python3
"""Fast runtime checks that catch NameError and ranking regressions missed by py_compile."""
from __future__ import annotations

from pipeline_runner import patch_update_jobs
from profile_ranker import load_profile, score_job
from smart_rank_jobs import hard_veto, shortlist


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

    # Page-shell words must not make an infrastructure title look frontend/mobile-heavy.
    infra = base_job("Software Engineer Graduate - AI Search Infra Team - 2027 Start")
    noisy_page = "Build inference infrastructure with CUDA and distributed systems. TikTok is a mobile video product with frontend experiences."
    infra_result = score_job(infra, profile, noisy_page)
    assert "frontend-heavy" not in infra_result["negative_signals"], infra_result
    assert "mobile specialization" not in infra_result["negative_signals"], infra_result

    # Defense-in-depth guards catch leakage even if an upstream cache misclassified it.
    assert hard_veto(base_job("NVIDIA 2027 Internships: Developer and Performance Technology"))
    assert hard_veto(base_job("Research Scientist - R&D - 2026"))
    assert not hard_veto(base_job("Machine Learning Engineer Graduate - 2027 Start"))

    # Explicit tracker company holds suppress an entire application-limited group.
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
    assert shortlist([held], profile, tracker) == []

    print(
        f"smoke ok: ml={ml_score}, backend={backend_score}, frontend={frontend_score}, "
        f"infra={infra_result['score']}"
    )


if __name__ == "__main__":
    main()
