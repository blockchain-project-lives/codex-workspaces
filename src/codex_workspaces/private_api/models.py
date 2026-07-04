from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AuthMaterial:
    account_id: str
    auth_path: Path
    access_token: str | None
    refresh_token: str | None
    raw_auth_hash: str
    openai_account_id: str | None = None


@dataclass
class QuotaInfo:
    status: str
    used_percent: float | None = None
    remaining_percent: float | None = None
    used: int | None = None
    limit: int | None = None
    remaining: int | None = None
    reset_at: str | None = None
    window_duration_mins: int | None = None
    plan: str | None = None
    source: str = "private-api"
    fetched_at: str | None = None
    cached: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "used_percent": self.used_percent,
            "remaining_percent": self.remaining_percent,
            "used": self.used,
            "limit": self.limit,
            "remaining": self.remaining,
            "reset_at": self.reset_at,
            "window_duration_mins": self.window_duration_mins,
            "plan": self.plan,
            "source": self.source,
            "fetched_at": self.fetched_at,
            "cached": self.cached,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QuotaInfo":
        return cls(
            status=str(data.get("status") or "unknown"),
            used_percent=as_float(data.get("used_percent")),
            remaining_percent=as_float(data.get("remaining_percent")),
            used=as_int(data.get("used")),
            limit=as_int(data.get("limit")),
            remaining=as_int(data.get("remaining")),
            reset_at=data.get("reset_at"),
            window_duration_mins=as_int(data.get("window_duration_mins")),
            plan=data.get("plan"),
            source=str(data.get("source") or "private-api"),
            fetched_at=data.get("fetched_at"),
            cached=bool(data.get("cached", False)),
            error=data.get("error"),
        )


@dataclass
class AccountRemoteInfo:
    email: str | None = None
    account_id: str | None = None
    user_id: str | None = None
    organization_id: str | None = None
    plan: str | None = None
    quota: QuotaInfo | None = None
    fetched_at: str | None = None


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
