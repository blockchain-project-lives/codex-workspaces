from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SENSITIVE_KEY_PARTS = (
    "token",
    "secret",
    "credential",
    "authorization",
    "refresh",
    "access",
    "cookie",
)


@dataclass
class AuthInspection:
    email: str | None = None
    account_id: str | None = None
    user_id: str | None = None
    organization_id: str | None = None
    plan: str | None = None
    auth_hash: str | None = None
    raw_keys: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def inspect_auth_file(auth_path: Path) -> AuthInspection:
    result = AuthInspection()
    try:
        result.auth_hash = file_hash(auth_path)
    except OSError as exc:
        result.warnings.append(f"could not read auth file: {exc}")
        return result

    try:
        data = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        result.warnings.append(f"could not parse auth JSON: {exc}")
        return result

    scan_value(data, result)
    return result


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def scan_value(value: Any, result: AuthInspection, key_path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if is_sensitive_key(key_text):
                continue
            if key_text not in result.raw_keys:
                result.raw_keys.append(key_text)
            scan_key_value(key_text, child, result, key_path)
            scan_value(child, result, (*key_path, key_text))
    elif isinstance(value, list):
        for child in value:
            scan_value(child, result, key_path)


def scan_key_value(key: str, value: Any, result: AuthInspection, key_path: tuple[str, ...]) -> None:
    if not isinstance(value, (str, int)):
        return
    text = str(value).strip()
    if not text or len(text) > 256:
        return
    lower_key = key.lower().replace("-", "_")
    parent_key = key_path[-1].lower().replace("-", "_") if key_path else ""

    if result.email is None and ("email" in lower_key or EMAIL_RE.match(text)) and EMAIL_RE.match(text):
        result.email = text
        return

    if result.account_id is None and (is_account_key(lower_key) or (lower_key == "id" and is_account_key(parent_key))) and looks_like_identifier(text):
        result.account_id = text
    if result.user_id is None and (is_user_key(lower_key) or (lower_key == "id" and is_user_key(parent_key))) and looks_like_identifier(text):
        result.user_id = text
    if result.organization_id is None and (is_org_key(lower_key) or (lower_key == "id" and is_org_key(parent_key))) and looks_like_identifier(text):
        result.organization_id = text
    if result.plan is None and is_plan_key(lower_key):
        result.plan = text


def is_sensitive_key(key: str) -> bool:
    lower = key.lower()
    return any(part in lower for part in SENSITIVE_KEY_PARTS)


def is_account_key(key: str) -> bool:
    normalized = key.replace("_", "")
    return normalized in {"accountid", "account"} or "account" in key


def is_user_key(key: str) -> bool:
    normalized = key.replace("_", "")
    return normalized in {"userid", "user", "sub"} or "user" in key


def is_org_key(key: str) -> bool:
    return "organization" in key or key == "org" or key.startswith("org_")


def is_plan_key(key: str) -> bool:
    return "plan" in key or "subscription" in key or "tier" in key


def looks_like_identifier(value: str) -> bool:
    if EMAIL_RE.match(value):
        return False
    return bool(re.match(r"^[A-Za-z0-9._:@-]{1,128}$", value))
