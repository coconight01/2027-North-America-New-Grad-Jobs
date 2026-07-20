from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from hashlib import sha1
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def canonical_url(url: str) -> str:
    """Drop tracking/query fragments while preserving the application path."""
    if not url:
        return ""
    p = urlsplit(url.strip())
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", ""))


@dataclass(slots=True)
class Job:
    company: str
    role: str
    category: str = "Other"
    location: str = "Unknown"
    country: str = "Unknown"
    graduation: str = "Unknown"
    start_date: str = "Unknown"
    degree: str = "Unknown"
    sponsorship: str = "Unknown"
    citizenship_required: str = "Unknown"
    salary: str = "Not listed"
    url: str = ""
    source: str = ""
    date_added: str = ""
    last_verified: str = ""
    status: str = "Open"
    match: str = "General new grad"
    description: str = ""

    def __post_init__(self) -> None:
        today = date.today().isoformat()
        self.company = self.company.strip()
        self.role = self.role.strip()
        self.location = self.location.strip() or "Unknown"
        self.url = canonical_url(self.url)
        self.date_added = self.date_added or today
        self.last_verified = self.last_verified or today

    @property
    def identity(self) -> str:
        base = "|".join([
            self.company.casefold(),
            self.role.casefold(),
            self.location.casefold(),
            self.url,
        ])
        return sha1(base.encode("utf-8")).hexdigest()[:16]

    def to_dict(self, include_description: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_description:
            data.pop("description", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Job":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in allowed})
