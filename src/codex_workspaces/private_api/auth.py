from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..store import auth_hash
from .errors import PrivateApiAuthError
from .models import AuthMaterial


def extract_auth_material(account_id: str, auth_path: Path) -> AuthMaterial:
    raw_hash = auth_hash(auth_path)
    try:
        data = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrivateApiAuthError("auth.json is not readable JSON") from exc
    if not isinstance(data, dict):
        raise PrivateApiAuthError("auth.json is not a JSON object")

    auth_mode = string_value(data.get("auth_mode"))
    if auth_mode != "chatgpt":
        raise PrivateApiAuthError("unsupported auth_mode; run codex login or an official codex command to refresh auth")

    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        raise PrivateApiAuthError("missing tokens in auth.json; run codex login or an official codex command to refresh auth")

    access_token = string_value(tokens.get("access_token"))
    refresh_token = string_value(tokens.get("refresh_token"))
    openai_account_id = string_value(tokens.get("account_id")) or string_value(data.get("account_id"))
    if not access_token:
        raise PrivateApiAuthError("missing tokens.access_token; run codex login or an official codex command to refresh auth")

    return AuthMaterial(
        account_id=account_id,
        auth_path=auth_path,
        access_token=access_token,
        refresh_token=refresh_token,
        raw_auth_hash=raw_hash,
        openai_account_id=openai_account_id,
    )


def string_value(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
