from __future__ import annotations

import re


SENSITIVE_WORDS = (
    "access_token",
    "refresh_token",
    "id_token",
    "session_token",
    "authorization",
    "cookie",
    "api_key",
    "bearer",
)
GENERIC_TOKEN_KEY_RE = r"(?i)\btoken\s*[:=]\s*[^\s,;]+"

SENSITIVE_VALUE_RE = r"[^\s,;]+"


class PrivateApiError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(redact_sensitive_text(message))
        self.message = redact_sensitive_text(message)


class PrivateApiDisabledError(PrivateApiError):
    pass


class PrivateApiAuthError(PrivateApiError):
    pass


class PrivateApiRateLimitedError(PrivateApiError):
    pass


class PrivateApiForbiddenError(PrivateApiError):
    pass


class PrivateApiUnsupportedResponseError(PrivateApiError):
    pass


class PrivateApiNetworkError(PrivateApiError):
    pass


def redact_sensitive_text(text: str) -> str:
    redacted = str(text)
    sensitive_keys = "|".join(re.escape(word) for word in SENSITIVE_WORDS)
    redacted = re.sub(rf"(?i)\bbearer\s+{SENSITIVE_VALUE_RE}", "[redacted] [redacted]", redacted)
    redacted = re.sub(rf"(?i)\b({sensitive_keys})\s*[:=]\s*{SENSITIVE_VALUE_RE}", "[redacted]=[redacted]", redacted)
    redacted = re.sub(rf"(?i)\b({sensitive_keys})\s+{SENSITIVE_VALUE_RE}", "[redacted] [redacted]", redacted)
    redacted = re.sub(GENERIC_TOKEN_KEY_RE, "[redacted]=[redacted]", redacted)
    for word in SENSITIVE_WORDS:
        redacted = re.sub(rf"(?i)\b{re.escape(word)}\b", "[redacted]", redacted)
    return redacted
