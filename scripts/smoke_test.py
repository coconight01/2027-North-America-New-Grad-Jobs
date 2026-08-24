#!/usr/bin/env python3
"""Fast runtime checks that catch NameError and ranking regressions missed by py_compile."""
from __future__ import annotations

from pipeline_runner import patch_update_jobs
from profile_ranker import load_profile, score_job


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
    print(f"smoke ok: ml={ml_score}, backend={backend_score}, frontend={frontend_score}")


if __name__ == "__main__":
    main()
