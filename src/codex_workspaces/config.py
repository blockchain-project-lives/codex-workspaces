from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional


RESTORE_POLICIES = {"workspace-default", "last-active", "keep-current"}
DEFAULT_APP_NAMES = ("ChatGPT", "Codex")


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


def detect_default_app_name(
    env: Mapping[str, str],
    *,
    applications_dir: Path = Path("/Applications"),
) -> str:
    explicit = env.get("CODEX_APP_NAME")
    if explicit:
        return explicit
    for name in DEFAULT_APP_NAMES:
        if (applications_dir / f"{name}.app").exists():
            return name
    return DEFAULT_APP_NAMES[0]


@dataclass(frozen=True)
class Config:
    app_name: str
    home_dir: Path
    root_dir: Path
    active_link: Path
    workspaces_dir: Path
    accounts_dir: Path
    backups_dir: Path
    cache_dir: Path
    lock_file: Path
    workspace_prefix: str
    quit_timeout: int
    lang: str
    restore_policy: str = "workspace-default"

    @classmethod
    def from_env(
        cls,
        env: Optional[Mapping[str, str]] = None,
        home: Optional[Path] = None,
        apple_language: Optional[str] = None,
    ) -> "Config":
        env = dict(os.environ if env is None else env)
        home_dir = Path(home or env.get("HOME") or Path.home()).expanduser()
        root_dir = Path(
            _expand_path(
                env.get("CODEX_WORKSPACES_ROOT") or str(home_dir / ".codex-workspaces")
            )
        )
        active_link = Path(
            _expand_path(
                env.get("CODEX_WORKSPACES_LINK") or str(home_dir / ".codex")
            )
        )
        workspaces_dir = Path(
            _expand_path(
                env.get("CODEX_WORKSPACES_WORKSPACES_DIR")
                or str(root_dir / "workspaces")
            )
        )
        accounts_dir = Path(
            _expand_path(
                env.get("CODEX_WORKSPACES_ACCOUNTS_DIR")
                or str(root_dir / "accounts")
            )
        )
        backups_dir = root_dir / "backups"
        cache_dir = root_dir / "cache"
        lock_file = root_dir / "lock"
        workspace_prefix = str(workspaces_dir) + os.sep
        quit_timeout = int(env.get("CODEX_QUIT_TIMEOUT") or "20")
        restore_policy = env.get("CODEX_WORKSPACES_RESTORE_POLICY") or "workspace-default"
        if restore_policy not in RESTORE_POLICIES:
            restore_policy = "workspace-default"

        return cls(
            app_name=detect_default_app_name(env),
            home_dir=home_dir,
            root_dir=root_dir,
            active_link=active_link,
            workspaces_dir=workspaces_dir,
            accounts_dir=accounts_dir,
            backups_dir=backups_dir,
            cache_dir=cache_dir,
            lock_file=lock_file,
            workspace_prefix=workspace_prefix,
            quit_timeout=quit_timeout,
            lang=detect_ui_lang(env, apple_language),
            restore_policy=restore_policy,
        )
