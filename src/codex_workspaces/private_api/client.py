from __future__ import annotations

import base64
import json
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import urljoin

from .errors import (
    PrivateApiAuthError,
    PrivateApiForbiddenError,
    PrivateApiNetworkError,
    PrivateApiRateLimitedError,
    PrivateApiUnsupportedResponseError,
)
from .models import AccountRemoteInfo, AuthMaterial, QuotaInfo


class PrivateApiProvider(Protocol):
    def get_quota(self, auth: AuthMaterial) -> QuotaInfo:
        ...

    def refresh_account(self, auth: AuthMaterial) -> AccountRemoteInfo:
        ...


class ConfiguredHttpPrivateApiProvider:
    def __init__(
        self,
        *,
        base_url: str | None,
        quota_endpoint: str | None,
        account_endpoint: str | None,
        timeout_seconds: int,
        user_agent: str,
    ) -> None:
        self.base_url = base_url
        self.quota_endpoint = quota_endpoint
        self.account_endpoint = account_endpoint
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    def get_quota(self, auth: AuthMaterial) -> QuotaInfo:
        if not self.base_url or not self.quota_endpoint:
            raise PrivateApiUnsupportedResponseError("quota endpoint is not configured")
        data = self.request_json(self.quota_endpoint, auth)
        return quota_from_response(data)

    def refresh_account(self, auth: AuthMaterial) -> AccountRemoteInfo:
        if not self.base_url or not self.account_endpoint:
            quota = self.get_quota(auth) if self.quota_endpoint else None
            return AccountRemoteInfo(quota=quota)
        data = self.request_json(self.account_endpoint, auth)
        remote = AccountRemoteInfo(
            email=pick(data, "email"),
            account_id=pick(data, "account_id", "accountId", "account"),
            user_id=pick(data, "user_id", "userId", "sub"),
            organization_id=pick(data, "organization_id", "organizationId", "org_id", "orgId"),
            plan=pick(data, "plan", "tier", "subscription"),
        )
        if self.quota_endpoint:
            remote.quota = self.get_quota(auth)
        return remote

    def request_json(self, endpoint: str, auth: AuthMaterial) -> dict:
        if not auth.access_token:
            raise PrivateApiAuthError("missing access token")
        if not auth.openai_account_id:
            raise PrivateApiAuthError("missing OpenAI account id in auth.json tokens.account_id")
        ensure_access_token_fresh(auth.access_token)
        request = urllib.request.Request(urljoin(self.base_url.rstrip("/") + "/", endpoint.lstrip("/")))
        request.add_header("User-Agent", self.user_agent)
        request.add_header("Accept", "application/json")
        request.add_header("Authorization", "Bearer " + auth.access_token)
        request.add_header("OpenAI-Account-Id", auth.openai_account_id)
        attempts = 0
        while True:
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = response.read().decode("utf-8")
                data = json.loads(payload)
                if not isinstance(data, dict):
                    raise PrivateApiUnsupportedResponseError("response is not a JSON object")
                return data
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    raise PrivateApiAuthError("unauthorized; run codex login or an official codex command to refresh auth") from exc
                if exc.code == 403:
                    raise PrivateApiForbiddenError("forbidden") from exc
                if exc.code == 429 and attempts < 1:
                    attempts += 1
                    time.sleep(1)
                    continue
                if exc.code == 429:
                    raise PrivateApiRateLimitedError("rate limited") from exc
                if exc.code >= 500:
                    raise PrivateApiNetworkError(f"server error {exc.code}") from exc
                raise PrivateApiUnsupportedResponseError(f"unsupported HTTP status {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                raise PrivateApiNetworkError("network error or timeout") from exc
            except json.JSONDecodeError as exc:
                raise PrivateApiUnsupportedResponseError("response is not valid JSON") from exc


def ensure_access_token_fresh(access_token: str) -> None:
    expires_at = jwt_exp(access_token)
    if expires_at is None:
        return
    if expires_at <= time.time() + 60:
        raise PrivateApiAuthError("access token expired; run codex login or an official codex command to refresh auth")


def jwt_exp(access_token: str) -> float | None:
    parts = access_token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    exp = data.get("exp")
    if isinstance(exp, (int, float)):
        return float(exp)
    return None


def quota_from_response(data: dict) -> QuotaInfo:
    rate_limit = data.get("rate_limit")
    if isinstance(rate_limit, dict) and isinstance(rate_limit.get("primary_window"), dict):
        primary_window = rate_limit["primary_window"]
        used_percent = first_number(primary_window, "used_percent", "usedPercent", "used_pct", "usage_percent")
        remaining_percent = max(0.0, 100.0 - used_percent) if used_percent is not None else None
        limit_reached = bool(rate_limit.get("limit_reached"))
        allowed = rate_limit.get("allowed")
        status = "ok"
        if limit_reached:
            status = "limit_reached"
        elif allowed is False:
            status = "not_allowed"
        return QuotaInfo(
            status=status,
            used_percent=used_percent,
            remaining_percent=remaining_percent,
            reset_at=normalize_reset_at(primary_window.get("reset_at"))
            or reset_after_to_iso(first_number(primary_window, "reset_after_seconds", "resetAfterSeconds")),
            window_duration_mins=seconds_to_minutes(first_number(primary_window, "limit_window_seconds", "limitWindowSeconds")),
            plan=wham_plan(data.get("credits")),
        )

    quota_data = data.get("quota") if isinstance(data.get("quota"), dict) else data
    used_percent = first_number(quota_data, "used_percent", "usedPercent", "used_pct", "usage_percent")
    remaining_percent = first_number(quota_data, "remaining_percent", "remainingPercent", "remaining_pct")
    used = first_int(quota_data, "used")
    limit = first_int(quota_data, "limit")
    remaining = first_int(quota_data, "remaining")
    if remaining_percent is None and used_percent is not None:
        remaining_percent = max(0.0, 100.0 - used_percent)
    return QuotaInfo(
        status=str(quota_data.get("status") or "ok"),
        used_percent=used_percent,
        remaining_percent=remaining_percent,
        used=used,
        limit=limit,
        remaining=remaining,
        reset_at=pick(quota_data, "reset_at", "resetAt", "resets_at"),
        window_duration_mins=first_int(quota_data, "window_duration_mins", "windowDurationMins", "window_mins"),
        plan=pick(quota_data, "plan", "tier", "subscription"),
    )


def normalize_reset_at(value) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            return str(value)
    return None


def seconds_to_minutes(value: float | None) -> int | None:
    if value is None:
        return None
    return int(value / 60)


def reset_after_to_iso(value: float | None) -> str | None:
    if value is None:
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=value)).isoformat()


def wham_plan(credits) -> str | None:
    if not isinstance(credits, dict):
        return None
    if credits.get("unlimited") is True:
        return "unlimited"
    if credits.get("balance") is not None:
        return "credits"
    return None


def pick(data: dict, *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def first_number(data: dict, *keys: str) -> float | None:
    for key in keys:
        try:
            if data.get(key) is not None:
                return float(data[key])
        except (TypeError, ValueError):
            pass
    return None


def first_int(data: dict, *keys: str) -> int | None:
    for key in keys:
        try:
            if data.get(key) is not None:
                return int(data[key])
        except (TypeError, ValueError):
            pass
    return None
