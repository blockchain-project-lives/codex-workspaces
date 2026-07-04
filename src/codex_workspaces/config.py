from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional


def _looks_zh(value: str) -> bool:
    return value.lower().replace("_", "-").startswith("zh")


def _looks_en(value: str) -> bool:
    return value.lower().replace("_", "-").startswith("en")


def detect_ui_lang(
    env: Mapping[str, str],
    apple_language: Optional[str] = None,
) -> str:
    forced = env.get("CODEX_WORKSPACES_LANG") or ""
    if _looks_zh(forced):
        return "zh"
    if _looks_en(forced):
        return "en"

    if apple_language:
        if _looks_zh(apple_language):
            return "zh"
        return "en"

    env_lang = env.get("LC_ALL") or env.get("LC_MESSAGES") or env.get("LANG") or ""
    return "zh" if _looks_zh(env_lang) else "en"


def _expand_path(value: str) -> str:
    return os.path.expandvars(os.path.expanduser(value))


@dataclass(frozen=True)
class Config:
    app_name: str
    home_dir: Path
    active_link: Path
    workspace_prefix: str
    quit_timeout: int
    lang: str

    @classmethod
    def from_env(
        cls,
        env: Optional[Mapping[str, str]] = None,
        home: Optional[Path] = None,
        apple_language: Optional[str] = None,
    ) -> "Config":
        env = dict(os.environ if env is None else env)
        home_dir = Path(home or env.get("HOME") or Path.home()).expanduser()
        active_link = Path(
            _expand_path(
                env.get("CODEX_WORKSPACES_LINK") or str(home_dir / ".codex")
            )
        )
        workspace_prefix = _expand_path(
            env.get("CODEX_WORKSPACES_PREFIX") or str(home_dir / ".codex-")
        )
        quit_timeout = int(env.get("CODEX_QUIT_TIMEOUT") or "20")

        return cls(
            app_name=env.get("CODEX_APP_NAME") or "Codex",
            home_dir=home_dir,
            active_link=active_link,
            workspace_prefix=workspace_prefix,
            quit_timeout=quit_timeout,
            lang=detect_ui_lang(env, apple_language),
        )
