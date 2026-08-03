#!/usr/bin/env python3
"""Collect high-value China roles from official MokaHR career boards."""
from __future__ import annotations

import base64
import csv
import html
import json
import re
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TODAY = date.today().isoformat()
TIMEOUT = 30
PAGE_SIZE = 50
API = "https://app.mokahr.com/api/outer/ats-apply/website/jobs/v2"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
SOURCES = [
    {
        "company": "NVIDIA China",
        "url": "https://app.mokahr.com/campus-recruitment/nvidia/47111?locale=zh-CN",
        "kind": "campus",
    },
    {
        "company": "DeepSeek / High-Flyer",
        "url": "https://app.mokahr.com/social-recruitment/high-flyer/140576?locale=zh-CN",
        "kind": "social",
    },
    {
        "company": "Ubiquant (九坤)",
        "url": "https://app.mokahr.com/campus_apply/ubiquantrecruit/37031?locale=zh-CN",
        "kind": "campus",
    },
]

INIT_DATA = re.compile(r'<input[^>]*id="init-data"[^>]*value="([^"]+)"', re.I)
PURE_HARDWARE = re.compile(
    r"PCB|ASIC|芯片|硬件|版图|物理设计|电路|模拟|射频|验证工程师|制造|封装|器件|"
    r"信号完整性|电源完整性|信号与电源完整性",
    re.I,
)
NON_TECH = re.compile(
    r"HR|人力|法务|财务|采购|行政|品牌|市场|运营|产品经理|校园大使|客户关系|税务",
    re.I,
)
QUANT = re.compile(
    r"quant|量化|策略|交易|风险|风控|因子|投资组合|portfolio", re.I
)
AI_INFRA = re.compile(
    r"AI基础设施|AI平台|Agent Infra|训练|推理|高性能|超算|分布式|"
    r"算子|通信|编译器|runtime|CUDA|GPU|存储|C\+\+|后端|服务端|框架",
    re.I,
)
AI_ML = re.compile(
    r"AI|机器学习|深度学习|算法|研究员|预训练|后训练|多模态|Agent|机器人|数据工程师",
    re.I,
)
SOFTWARE = re.compile(
    r"软件|研发|开发|工程师|研究员|算法|数据科学|Quant", re.I
)
CONFIRMED_NG = re.compile(
    r"2027.{0,8}(?:校园招聘|校招)|(?:校园招聘|校招).{0,8}2027|"
    r"梧桐计划|揽月计划|应届|new grad|graduate",
    re.I,
)
INTERNSHIP_ONLY = re.compile(r"实习|intern", re.I)
FULL_TIME = re.compile(r"全职|full[- ]?time", re.I)


def text(value: object) -> str:
    return BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True)


def parse_init(page: str) -> dict:
    match = INIT_DATA.search(page)
    if not match:
        raise ValueError("MokaHR init-data was not present")
    return json.loads(html.unescape(match.group(1)))


def decrypt_envelope(envelope: dict, iv_text: str) -> dict:
    key = str(envelope.get("necromancer") or "").encode()
    iv = iv_text.encode()
    encrypted = base64.b64decode(envelope.get("data") or "")
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(encrypted) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return json.loads(unpadder.update(padded) + unpadder.finalize())


def fetch_source(source: dict) -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)
    response = session.get(source["url"], timeout=TIMEOUT, allow_redirects=True)
    response.raise_for_status()
    init = parse_init(response.text)
    fallback = init.get("jobs") or []
    org_id = str((init.get("org") or {}).get("id") or "")
    site_id = str(init.get("siteId") or (init.get("org") or {}).get("siteId") or "")
    if not org_id or not site_id or not init.get("aesIv"):
        return fallback
    try:
        api_response = session.post(
            f"{API}?orgId={org_id}",
            json={
                "orgId": org_id,
                "siteId": site_id,
                "limit": PAGE_SIZE,
                "offset": 0,
                "needStat": True,
                "locale": "zh-CN",
            },
            headers={
                "Accept": "application/json,*/*",
                "Content-Type": "application/json",
                "Origin": "https://app.mokahr.com",
                "Referer": source["url"],
            },
            timeout=TIMEOUT,
        )
        api_response.raise_for_status()
        decoded = decrypt_envelope(api_response.json(), str(init["aesIv"]))
        jobs = ((decoded.get("data") or {}).get("jobs") or [])
        return jobs or fallback
    except (requests.RequestException, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return fallback


def location(job: dict) -> str:
    values = []
    for item in job.get("locations") or []:
        if not isinstance(item, dict):
            continue
        province = text(item.get("provinceName"))
        city = text(item.get("cityName"))
        country = text(item.get("country"))
        value = " · ".join(part for part in [province, city] if part) or country
        if value and value not in values:
            values.append(value)
    return " / ".join(values) or "China"


def track_for(title: str) -> str:
    if QUANT.search(title):
        return "Quantitative Finance"
    if AI_INFRA.search(title):
        return "AI Infrastructure / Systems"
    if AI_ML.search(title):
        return "AI / ML"
    return "Software Engineering"


def eligibility_for(title: str, source: dict) -> str:
    if CONFIRMED_NG.search(title):
        return "Confirmed campus"
    if source["kind"] == "campus":
        return "Likely campus"
    return "Review eligibility"


def keep_job(job: dict, source: dict) -> bool:
    title = text(job.get("title"))
    if not title or str(job.get("status") or "open").casefold() not in {"open", "active"}:
        return False
    if PURE_HARDWARE.search(title) or NON_TECH.search(title):
        return False
    if INTERNSHIP_ONLY.search(title) and not FULL_TIME.search(title):
        return False
    if not SOFTWARE.search(title):
        return False
    if source["company"] == "NVIDIA China" and not CONFIRMED_NG.search(title):
        return False
    if source["company"] == "Ubiquant (九坤)" and not (
        "梧桐计划" in title or "揽月计划" in title
    ):
        return False
    return True


def posted_date(job: dict) -> str:
    for key in ("publishedAt", "openedAt", "createdAt"):
        value = str(job.get(key) or "")
        match = re.match(r"20\d{2}-\d{2}-\d{2}", value)
        if match:
            return match.group(0)
    return TODAY


def role_url(source: dict, job_id: str) -> str:
    return source["url"].split("?", 1)[0].rstrip("/") + f"#/job/{job_id}"


def collect() -> tuple[list[dict], list[str]]:
    rows = []
    warnings = []
    for source in SOURCES:
        try:
            jobs = fetch_source(source)
        except (requests.RequestException, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            warnings.append(f'{source["company"]}: {type(exc).__name__}')
            continue
        for job in jobs:
            if not isinstance(job, dict) or not keep_job(job, source):
                continue
            job_id = str(job.get("id") or job.get("jobId") or "").strip()
            if not job_id:
                continue
            title = text(job.get("title"))
            rows.append(
                {
                    "posted_date": posted_date(job),
                    "company": source["company"],
                    "role": title,
                    "track": track_for(title),
                    "location": location(job),
                    "employment_type": text(job.get("commitment")) or (
                        "Full-time / Internship" if FULL_TIME.search(title) else "Full-time"
                    ),
                    "eligibility": eligibility_for(title, source),
                    "salary": "Not published",
                    "source": "Official MokaHR",
                    "url": role_url(source, job_id),
                    "job_id": job_id,
                    "status": "Open",
                    "date_added": TODAY,
                }
            )
    deduped = {}
    for row in rows:
        key = (
            row["company"].casefold(),
            re.sub(r"\W+", " ", row["role"].casefold()).strip(),
            row["location"].casefold(),
        )
        deduped.setdefault(key, row)
    rank = {"Confirmed campus": 0, "Likely campus": 1, "Review eligibility": 2}
    result = list(deduped.values())
    result.sort(
        key=lambda row: (
            -int(row["posted_date"].replace("-", "")),
            rank.get(row["eligibility"], 3),
            {"AI Infrastructure / Systems": 0, "Quantitative Finance": 1, "AI / ML": 2}.get(
                row["track"], 3
            ),
            row["company"].casefold(),
            row["role"].casefold(),
        )
    )
    return result, warnings


def save(rows: list[dict], warnings: list[str]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    fields = [
        "posted_date", "company", "role", "track", "location", "employment_type",
        "eligibility", "salary", "source", "url", "job_id", "status", "date_added",
    ]
    with (DATA / "china_jobs.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (DATA / "china_jobs.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    parts = [
        "# China High-Value New-Grad & Early-Career Jobs",
        "",
        f"> Last automated update: **{TODAY}** · Open roles: **{len(rows)}**",
        "",
        "This list is kept separate from the North America list: U.S. sponsorship and the $100k hard floor do not apply to China roles. Most official China boards do not publish compensation, so missing salary is never treated as low salary.",
        "",
        "Pure hardware, non-technical, and internship-only roles are filtered. Official social-recruiting roles with unclear graduate eligibility are retained as **Review eligibility** and ranked after explicit campus programs.",
        "",
        "| Posted | Company | Role | Track | Location | Type | Eligibility |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        role = f'[{row["role"].replace("|", "/")}]({row["url"]})'
        values = [
            row["posted_date"],
            f'**{row["company"].replace("|", "/")}**',
            role,
            row["track"],
            row["location"].replace("|", "/"),
            row["employment_type"].replace("|", "/"),
            row["eligibility"],
        ]
        parts.append("| " + " | ".join(values) + " |")
    if warnings:
        parts += ["", "> Source warnings: " + "; ".join(warnings)]
    parts += [
        "",
        "## Source policy",
        "",
        "- Current official sources: NVIDIA China, DeepSeek / High-Flyer, and Ubiquant.",
        "- GitHub and university career posts may be used for discovery, but official company links are preferred for applications.",
        "- **Confirmed campus** means the official title names a campus/new-grad program; **Review eligibility** means the role is valuable but prior-experience requirements must be checked manually.",
        "",
    ]
    (ROOT / "CHINA.md").write_text("\n".join(parts), encoding="utf-8")


def main() -> None:
    rows, warnings = collect()
    save(rows, warnings)
    print(f"Collected {len(rows)} China roles; warnings: {', '.join(warnings) or 'none'}")


if __name__ == "__main__":
    main()
