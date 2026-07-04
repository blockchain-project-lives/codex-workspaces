from __future__ import annotations

import hashlib
import os
import platform as platform_module
import re
import shlex
import shutil
import stat
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, TextIO

from .config import Config
from .errors import CodexWorkspacesError
from .platforms import SystemPlatform
from .store import AccountMeta, WorkspaceMeta, WorkspaceStore, copy_auth, iso_now
from .stats import StatsError, WorkspaceStats, compute_workspace_stats

WORKSPACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
NOTE_FILE = ".codex-workspace-note"


def strip_workspace_name(value: str) -> str:
    name = re.split(r"[\\/]+", value.rstrip("\\/"))[-1]
    if name.startswith(".codex-"):
        name = name[len(".codex-") :]
    return name


def validate_workspace_name(name: str) -> None:
    if not name:
        raise CodexWorkspacesError("Workspace name cannot be empty")
    if name in {".", ".."}:
        raise CodexWorkspacesError(f"Workspace name cannot be {name}")
    if not WORKSPACE_RE.match(name):
        raise CodexWorkspacesError(
            "Workspace name can only contain letters, numbers, dots, underscores, and hyphens: "
            + name
        )


def workspace_dir(config: Config, name: str) -> Path:
    clean_name = strip_workspace_name(name)
    validate_workspace_name(clean_name)
    return config.workspaces_dir / clean_name


@dataclass(frozen=True)
class CurrentTarget:
    kind: str
    path: Optional[Path] = None


@dataclass(frozen=True)
class LegacyWorkspaceCandidate:
    name: str
    source: Path
    target: Path
    account_id: Optional[str]


@dataclass(frozen=True)
class LegacyAccountCandidate:
    name: str
    source: Path
    target_id: str


class WorkspaceManager:
    def __init__(
        self,
        config: Config,
        platform_service: Optional[SystemPlatform] = None,
        stdout: Optional[TextIO] = None,
        stderr: Optional[TextIO] = None,
        stdin: Optional[TextIO] = None,
    ) -> None:
        self.config = config
        self.platform = platform_service or SystemPlatform()
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        self.stdin = stdin or sys.stdin
        self.store = WorkspaceStore(config)

    def is_zh(self) -> bool:
        return self.config.lang == "zh"

    def message(self, zh: str, en: str) -> str:
        return zh if self.is_zh() else en

    def fail(self, zh: str, en: str) -> None:
        raise CodexWorkspacesError(self.message(zh, en))

    def info(self, text: str = "") -> None:
        print(text, file=self.stdout)

    def bold(self, text: str) -> str:
        isatty = getattr(self.stdout, "isatty", lambda: False)
        return f"\033[1m{text}\033[0m" if isatty() else text

    def workspace_dir(self, name: str) -> Path:
        return workspace_dir(self.config, name)

    def real_dir(self, path: Path) -> Path:
        if self.platform.is_directory_link(path):
            return path.resolve(strict=False)
        if path.is_dir():
            return path.resolve(strict=True)
        return path

    def current_target(self) -> CurrentTarget:
        active = self.config.active_link
        if self.platform.is_directory_link(active):
            return CurrentTarget("target", self.real_dir(active))
        if active.exists():
            return CurrentTarget("not-a-link", active)
        return CurrentTarget("missing")

    def workspace_dirs(self) -> List[Path]:
        if not self.config.workspaces_dir.is_dir():
            return []
        return sorted(path for path in self.config.workspaces_dir.iterdir() if path.is_dir())

    def same_path(self, left: Path, right: Path) -> bool:
        left_s = os.path.normcase(os.path.realpath(left))
        right_s = os.path.normcase(os.path.realpath(right))
        return left_s == right_s

    def current_name(self, target: Path) -> Optional[str]:
        for directory in self.workspace_dirs():
            if self.same_path(self.real_dir(directory), target):
                return strip_workspace_name(str(directory))
        return None

    def list_workspaces(self) -> None:
        current = self.current_target()
        self.info(self.bold(self.message("Codex 工作区", "Codex workspaces")))
        found = False
        if self.workspace_dirs():
            self.info(
                self.message(
                    f" {'':1} {'名称':<16} {'大小':>8}  {'最后修改':<16} {'备注':<24} 路径",
                    f" {'':1} {'name':<16} {'size':>8}  {'modified':<16} {'note':<24} path",
                )
            )
        for directory in self.workspace_dirs():
            found = True
            name = strip_workspace_name(str(directory))
            marker = " "
            if current.kind == "target" and current.path:
                marker = "*" if self.same_path(self.real_dir(directory), current.path) else " "
            self.info(
                f" {marker} {name:<16} {self.format_size(self.directory_size(directory)):>8}  "
                f"{self.format_mtime(directory):<16} {self.format_note(self.workspace_note(directory)):<24} {directory}"
            )

        if not found:
            self.info(
                self.message(
                    "未找到工作区目录。可以先执行: codex-workspaces init work",
                    "No workspace directories found. You can initialize one with: codex-workspaces init work",
                )
            )

        self.info()
        if current.kind == "missing":
            self.info(
                self.message(
                    f"当前 {self.config.active_link} 不存在。",
                    f"Current {self.config.active_link} does not exist.",
                )
            )
        elif current.kind == "not-a-link":
            self.info(
                self.message(
                    f"当前 {self.config.active_link} 存在，但不是软链接，切换前需要手动处理。",
                    f"Current {self.config.active_link} exists, but it is not a symlink. Please handle it manually before switching.",
                )
            )
        elif current.path:
            name = self.current_name(current.path)
            if name:
                self.info(
                    self.message(
                        f"当前工作区: {name} -> {current.path}",
                        f"Current workspace: {name} -> {current.path}",
                    )
                )
            else:
                self.info(
                    self.message(
                        f"当前工作区: 未匹配到工作区目录 -> {current.path}",
                        f"Current workspace: no matching workspace directory -> {current.path}",
                    )
                )

    def directory_size(self, directory: Path) -> int:
        total = 0
        stack = [directory]
        while stack:
            current = stack.pop()
            try:
                for entry in os.scandir(current):
                    try:
                        stat_result = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    else:
                        total += stat_result.st_size
            except OSError:
                continue
        return total

    def format_size(self, size: int) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        value = float(size)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)} {unit}"
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"

    def format_mtime(self, path: Path) -> str:
        try:
            timestamp = path.stat().st_mtime
        except OSError:
            return "-"
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")

    def note_path(self, directory: Path) -> Path:
        return directory / NOTE_FILE

    def workspace_note(self, directory: Path) -> str:
        try:
            return self.note_path(directory).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""
        except OSError:
            return ""

    def format_note(self, note: str) -> str:
        clean_note = " ".join(note.split())
        if len(clean_note) <= 24:
            return clean_note
        return clean_note[:21] + "..."

    def show_current(self) -> None:
        current = self.current_target()
        if current.kind == "missing":
            self.fail(
                f"{self.config.active_link} 不存在",
                f"{self.config.active_link} does not exist",
            )
        if current.kind == "not-a-link":
            self.fail(
                f"{self.config.active_link} 存在，但不是软链接",
                f"{self.config.active_link} exists, but it is not a symlink",
            )
        assert current.path is not None
        name = self.current_name(current.path) or "unknown"
        self.info(f"{name} -> {current.path}")

    def workspace_info(self, name: str) -> None:
        clean_name = strip_workspace_name(name)
        validate_workspace_name(clean_name)
        directory = self.workspace_dir(clean_name)
        if not directory.is_dir():
            self.fail(f"工作区不存在: {directory}", f"Workspace does not exist: {directory}")
        current = self.current_target()
        is_active = current.kind == "target" and current.path is not None and self.same_path(self.real_dir(directory), current.path)
        meta = self.store.ensure_workspace_meta(clean_name, directory)
        self.info(self.bold(self.message(f"工作区: {clean_name}", f"Workspace: {clean_name}")))
        self.info(f"name: {clean_name}")
        self.info(f"path: {directory}")
        self.info(f"active: {self._yes_no(is_active)}")
        self.info(f"size: {self.format_size(self.directory_size(directory))}")
        self.info(f"modified: {self.format_mtime(directory)}")
        self.info(f"note: {self.workspace_note(directory) or '-'}")
        self.info(f"default_account_id: {meta.default_account_id or '-'}")
        self.info(f"active_account_id: {meta.active_account_id or '-'}")
        self.info(f"restore_default_on_enter: {self._yes_no(meta.restore_default_on_enter)}")
        self.info(f"created_at: {meta.created_at}")
        self.info(f"updated_at: {meta.updated_at}")
        self.info(f"last_used_at: {meta.last_used_at or '-'}")

    def doctor(self) -> None:
        current = self.current_target()
        workspaces = self.workspace_dirs()
        prefix_parent = Path(self.config.workspace_prefix).parent

        self.info(self.bold(self.message("Codex 工作区诊断", "Codex workspaces doctor")))
        self.info(f"python: {platform_module.python_version()} ({sys.executable})")
        self.info(f"platform: {platform_module.system()} {platform_module.release()}")
        self.info(f"app: {self.config.app_name}")
        self.info(f"root: {self.config.root_dir}")
        self.info(f"active link: {self.config.active_link}")
        self.info(f"workspaces dir: {self.config.workspaces_dir}")
        self.info(f"accounts dir: {self.config.accounts_dir}")
        self.info(f"workspace parent: {prefix_parent}")
        self.info(f"workspace parent exists: {self._yes_no(prefix_parent.exists())}")
        self.info(f"workspace parent writable: {self._yes_no(os.access(prefix_parent, os.W_OK))}")
        self.info(f"workspaces found: {len(workspaces)}")

        if current.kind == "missing":
            self.info("current state: missing")
        elif current.kind == "not-a-link":
            self.info("current state: exists but is not a link")
        elif current.path:
            name = self.current_name(current.path) or "unknown"
            self.info(f"current state: {name} -> {current.path}")

        self.info(f"directory link support: {self._yes_no(self._can_attempt_directory_links())}")
        self.info(f"app control support: {self._yes_no(self.platform.supports_app_control)}")
        running = self.platform.app_running_status(self.config.app_name)
        running_text = "unknown" if running is None else self._yes_no(running)
        self.info(f"app running: {running_text}")
        self.info(f"codex terminal detected: {self._yes_no(self.platform.is_codex_terminal())}")
        self.info(f"restore policy: {self.config.restore_policy}")
        self.render_account_doctor()

    def render_account_doctor(self) -> None:
        self.store.ensure_layout()
        issues: list[str] = []
        accounts = {account.id: account for account in self.store.list_accounts()}
        account_refs: dict[str, set[str]] = {account_id: set() for account_id in accounts}

        for directory in self.workspace_dirs():
            name = strip_workspace_name(str(directory))
            meta = self.store.ensure_workspace_meta(name, directory)
            auth_path = directory / "auth.json"
            if auth_path.is_file() and not meta.default_account_id:
                issues.append(f"workspace {name} has auth.json but no default account")
            for kind, account_id in (("default", meta.default_account_id), ("active", meta.active_account_id)):
                if not account_id:
                    continue
                account_refs.setdefault(account_id, set()).add(f"{kind}:{name}")
                if account_id not in accounts or not self.store.account_auth_path(account_id).is_file():
                    issues.append(f"workspace {name} {kind}_account_id points to missing account {account_id}")

        for account_id, refs in sorted(account_refs.items()):
            if account_id in accounts and not refs:
                issues.append(f"account {account_id} is not referenced by any workspace")

        for path, expected in (
            (self.config.root_dir, 0o700),
            (self.config.workspaces_dir, 0o700),
            (self.config.accounts_dir, 0o700),
        ):
            if path.exists() and self.path_mode(path) is not None and self.path_mode(path) != expected:
                issues.append(f"{path} permission is {self.path_mode(path):03o}, expected {expected:03o}")

        for account_id in accounts:
            for path in (self.store.account_dir(account_id), self.store.account_auth_path(account_id), self.store.account_meta_path(account_id)):
                mode = self.path_mode(path)
                if mode is None:
                    continue
                expected = 0o700 if path.is_dir() else 0o600
                if mode != expected:
                    issues.append(f"{path} permission is {mode:03o}, expected {expected:03o}")

        legacy_dirs = self.scan_legacy_workspaces()
        legacy_accounts = self.config.home_dir / ".codex-accounts"
        if legacy_dirs:
            issues.append(f"legacy workspace directories still exist: {len(legacy_dirs)}")
        if legacy_accounts.exists():
            issues.append(f"legacy accounts directory still exists: {legacy_accounts}")

        self.info(f"accounts found: {len(accounts)}")
        if not issues:
            self.info("account diagnostics: ok")
            return
        self.info("account diagnostics:")
        for issue in issues:
            self.info(f"  ! {issue}")

    def path_mode(self, path: Path) -> Optional[int]:
        try:
            return stat.S_IMODE(path.stat().st_mode)
        except OSError:
            return None

    def show_stats(self, name: Optional[str] = None, days: int = 7) -> None:
        clean_name, directory = self.stats_target(name)
        try:
            stats = compute_workspace_stats(clean_name, directory, days)
        except StatsError as exc:
            self.fail(f"无法读取统计数据: {exc}", f"Could not read stats: {exc}")

        self.info(self.bold(self.message(f"Codex 工作区统计: {stats.name}", f"Codex workspace stats: {stats.name}")))
        self.info(f"source: {stats.source}")
        self.info(self.message("说明: 本命令只读本地 Codex SQLite，不访问 quota/refresh 私有接口。", "note: this only reads local Codex SQLite; it does not call quota/refresh private APIs."))
        self.info()

        if not stats.sessions:
            self.info(self.message("没有记录到 token 用量。", "No token usage recorded."))
            return

        self.info(f"sessions: {stats.total_sessions:,}")
        self.info(f"total tokens: {stats.total_tokens:,}")
        self.info(f"last 7 days: {stats.last_7d_tokens:,} ({stats.last_7d_sessions} sessions)")
        self.info(f"last 30 days: {stats.last_30d_tokens:,} ({stats.last_30d_sessions} sessions)")
        self.info()
        self.render_model_stats(stats)
        self.info()
        self.render_daily_stats(stats)
        self.info()
        self.render_recent_sessions(stats)

    def stats_target(self, name: Optional[str]) -> tuple[str, Path]:
        if name:
            clean_name = strip_workspace_name(name)
            validate_workspace_name(clean_name)
            directory = self.workspace_dir(clean_name)
            if not directory.is_dir():
                self.fail(f"工作区不存在: {directory}", f"Workspace does not exist: {directory}")
            return clean_name, directory

        current = self.current_target()
        if current.kind == "missing":
            self.fail(
                "当前工作区不存在，请指定工作区名。",
                "Current workspace does not exist; pass a workspace name.",
            )
        if current.kind == "not-a-link":
            self.fail(
                "当前工作区不是链接，请指定工作区名。",
                "Current workspace is not a link; pass a workspace name.",
            )
        assert current.path is not None
        return self.current_name(current.path) or "current", current.path

    def render_model_stats(self, stats: WorkspaceStats) -> None:
        self.info(self.message("按模型:", "by model:"))
        for model, tokens in sorted(stats.by_model.items(), key=lambda item: (-item[1], item[0])):
            self.info(f"  {model:<22} {tokens:>14,}  {self.bar(tokens, stats.total_tokens)}")

    def render_daily_stats(self, stats: WorkspaceStats) -> None:
        self.info(self.message(f"每日 token 最近 {len(stats.daily)} 天:", f"daily tokens last {len(stats.daily)} days:"))
        peak = max((entry.tokens for entry in stats.daily), default=0)
        for entry in stats.daily:
            self.info(f"  {entry.day.isoformat()}  {entry.tokens:>14,}  {self.bar(entry.tokens, peak)} ({entry.sessions})")

    def render_recent_sessions(self, stats: WorkspaceStats) -> None:
        self.info(self.message("最近会话:", "recent sessions:"))
        for session in stats.sessions[:10]:
            timestamp = session.created_at.astimezone().strftime("%m-%d %H:%M") if session.created_at else "?"
            title = " ".join(session.title.split())
            if len(title) > 36:
                title = title[:33] + "..."
            self.info(f"  {timestamp:<11} {title:<36} {session.tokens:>10,}  {session.model[:18]}")

    def bar(self, value: int, total: int, width: int = 20) -> str:
        if value <= 0 or total <= 0:
            return ""
        count = max(1, int(value / total * width))
        return "#" * min(width, count)

    def _yes_no(self, value: bool) -> str:
        return self.message("是" if value else "否", "yes" if value else "no")

    def _can_attempt_directory_links(self) -> bool:
        return hasattr(os, "symlink") or self.platform.is_windows

    def stop_codex(self, force: bool = False, argv: Optional[Sequence[str]] = None) -> None:
        if self.platform.is_codex_terminal():
            if self.platform.supports_external_terminal_delegation:
                self.platform.delegate_to_external_terminal(
                    self.config,
                    self.message("关闭 Codex", "stop Codex"),
                    list(argv or (["stop", "--force"] if force else ["stop"])),
                    self.stdout,
                )
                return
            self.require_external_terminal("stop")

        if not self.platform.supports_app_control:
            self.fail(
                "当前平台不支持自动关闭 Codex App。切换工作区时可使用 --no-stop。",
                "App stop is only supported on macOS. Use --no-stop when switching workspaces on this platform.",
            )
        self.platform.stop_app(
            self.config.app_name,
            self.config.quit_timeout,
            force,
            self.stdout,
        )

    def start_codex(self) -> None:
        self.require_external_terminal("start")
        if not self.platform.supports_app_control:
            self.fail(
                "当前平台不支持自动启动 Codex App。",
                "App start is only supported on macOS.",
            )
        self.info(self.message(f"正在启动 {self.config.app_name} ...", f"Starting {self.config.app_name} ..."))
        self.platform.start_app(self.config.app_name)

    def restart_codex(self, force: bool = False, argv: Optional[Sequence[str]] = None) -> None:
        if self.platform.is_codex_terminal():
            if self.platform.supports_external_terminal_delegation:
                self.platform.delegate_to_external_terminal(
                    self.config,
                    self.message("重启 Codex", "restart Codex"),
                    list(argv or (["restart", "--force"] if force else ["restart"])),
                    self.stdout,
                )
                return
            self.require_external_terminal("restart")
        self.stop_codex(force)
        self.start_codex()

    def require_external_terminal(self, action: str) -> None:
        if not self.platform.is_codex_terminal():
            return
        zh_actions = {
            "stop": "关闭 Codex",
            "start": "启动 Codex",
            "restart": "重启 Codex",
            "switch": "切换工作区",
            "migration": "迁移工作区目录",
            "account login": "登录账号",
        }
        self.fail(
            f"不能在 Codex 内置 Terminal 中执行{zh_actions.get(action, action)}。请打开外部系统 Terminal，在 Codex 外部运行该命令。",
            f"Cannot run {action} from the built-in Codex terminal. Open an external system Terminal and run this command outside Codex.",
        )

    def ensure_app_not_running_for_migration(self) -> None:
        status = self.platform.app_running_status(self.config.app_name)
        if status is True:
            self.fail(
                f"{self.config.app_name} 正在运行。为避免配置损坏，请先从外部 Terminal 关闭 {self.config.app_name} 后再执行迁移。",
                f"{self.config.app_name} is running. To avoid corrupting config files, quit {self.config.app_name} from an external terminal before migration.",
            )
        if status is None and self.platform.supports_app_control:
            self.fail(
                f"无法确认 {self.config.app_name} 是否运行。为避免配置损坏，请先从外部 Terminal 确认 {self.config.app_name} 已关闭后再执行迁移。",
                f"Cannot confirm whether {self.config.app_name} is running. To avoid corrupting config files, confirm {self.config.app_name} is closed from an external terminal before migration.",
            )

    def legacy_workspace_prefix(self, from_prefix: Optional[str] = None) -> Path:
        if from_prefix:
            return Path(os.path.expandvars(os.path.expanduser(from_prefix)))
        return self.config.home_dir / ".codex-"

    def scan_legacy_workspaces(self, from_prefix: Optional[str] = None) -> list[LegacyWorkspaceCandidate]:
        prefix = self.legacy_workspace_prefix(from_prefix)
        parent = prefix.parent
        prefix_name = prefix.name
        if not parent.is_dir():
            return []

        candidates: list[LegacyWorkspaceCandidate] = []
        for path in sorted(parent.iterdir()):
            if not path.is_dir() or self.platform.is_directory_link(path):
                continue
            if not path.name.startswith(prefix_name):
                continue
            raw_name = path.name[len(prefix_name) :]
            if raw_name in {"", "accounts", "workspaces"}:
                continue
            try:
                validate_workspace_name(raw_name)
            except CodexWorkspacesError:
                continue
            target = self.workspace_dir(raw_name)
            account_id = self.unique_account_id("acct_" + raw_name, path) if (path / "auth.json").is_file() else None
            candidates.append(LegacyWorkspaceCandidate(raw_name, path, target, account_id))
        return candidates

    def scan_legacy_accounts(self, legacy_accounts_dir: Path) -> list[LegacyAccountCandidate]:
        if not legacy_accounts_dir.is_dir():
            return []
        candidates: list[LegacyAccountCandidate] = []
        for directory in sorted(legacy_accounts_dir.iterdir()):
            if not directory.is_dir() or not (directory / "auth.json").is_file():
                continue
            try:
                target_id = self.unique_account_id(directory.name, directory)
                name = self.account_name_from_input(directory.name)
            except CodexWorkspacesError:
                continue
            candidates.append(LegacyAccountCandidate(name, directory, target_id))
        return candidates

    def dedupe_legacy_account_candidates(
        self,
        candidates: Sequence[LegacyAccountCandidate],
        reserved_ids: set[str],
    ) -> list[LegacyAccountCandidate]:
        deduped: list[LegacyAccountCandidate] = []
        for candidate in candidates:
            target_id = candidate.target_id
            if target_id in reserved_ids:
                base = self.account_id_from_input(candidate.name)
                suffix = hashlib.sha1(str(candidate.source).encode("utf-8")).hexdigest()[:6]
                target_id = f"{base}_{suffix}"
                index = 2
                while target_id in reserved_ids or self.store.account_dir(target_id).exists():
                    target_id = f"{base}_{suffix}_{index}"
                    index += 1
            reserved_ids.add(target_id)
            deduped.append(LegacyAccountCandidate(candidate.name, candidate.source, target_id))
        return deduped

    def unique_account_id(self, value: str, salt: Path) -> str:
        base = self.account_id_from_input(value)
        if not self.store.account_dir(base).exists():
            return base
        suffix = hashlib.sha1(str(salt).encode("utf-8")).hexdigest()[:6]
        candidate = f"{base}_{suffix}"
        if not self.store.account_dir(candidate).exists():
            return candidate
        index = 2
        while self.store.account_dir(f"{candidate}_{index}").exists():
            index += 1
        return f"{candidate}_{index}"

    def migration_backup_dir(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = self.config.backups_dir / stamp / "before-migrate"
        suffix = 2
        while backup_dir.exists():
            backup_dir = self.config.backups_dir / f"{stamp}-{suffix}" / "before-migrate"
            suffix += 1
        return backup_dir

    def copy_path_for_backup(self, source: Path, destination: Path) -> int:
        return self.copy_supported_tree(source, destination, context="backup")

    def copy_supported_tree(self, source: Path, destination: Path, *, context: str) -> int:
        try:
            source_stat = source.lstat()
        except OSError as exc:
            self.info(self.message(f"跳过无法读取的路径: {source} ({exc})", f"Skipping unreadable path: {source} ({exc})"))
            return 1

        mode = source_stat.st_mode
        destination.parent.mkdir(parents=True, exist_ok=True)
        if stat.S_ISLNK(mode):
            destination.symlink_to(os.readlink(source))
            return 0
        if stat.S_ISDIR(mode):
            destination.mkdir(exist_ok=True)
            try:
                shutil.copystat(source, destination, follow_symlinks=False)
            except OSError:
                pass
            skipped = 0
            for child in sorted(source.iterdir()):
                skipped += self.copy_supported_tree(child, destination / child.name, context=context)
            return skipped
        if stat.S_ISREG(mode):
            shutil.copy2(source, destination)
            return 0

        self.info(
            self.message(
                f"跳过不支持复制的特殊文件: {source}",
                f"Skipping unsupported special file during {context}: {source}",
            )
        )
        return 1

    def backup_migration_sources(
        self,
        backup_dir: Path,
        workspaces: Sequence[LegacyWorkspaceCandidate],
        legacy_accounts_dir: Optional[Path],
    ) -> int:
        skipped = 0
        if self.config.active_link.exists() or self.config.active_link.is_symlink():
            self.info(self.message(f"备份当前链接: {self.config.active_link}", f"Backing up active link: {self.config.active_link}"))
            skipped += self.copy_path_for_backup(self.config.active_link, backup_dir / "codex")
        for candidate in workspaces:
            self.info(self.message(f"备份旧工作区: {candidate.source}", f"Backing up legacy workspace: {candidate.source}"))
            skipped += self.copy_path_for_backup(candidate.source, backup_dir / "legacy-workspaces" / candidate.source.name)
        if legacy_accounts_dir and legacy_accounts_dir.is_dir():
            self.info(self.message(f"备份旧账号目录: {legacy_accounts_dir}", f"Backing up legacy accounts: {legacy_accounts_dir}"))
            skipped += self.copy_path_for_backup(legacy_accounts_dir, backup_dir / "legacy-accounts" / legacy_accounts_dir.name)
        return skipped

    def import_legacy_account_candidate(self, candidate: LegacyAccountCandidate) -> str:
        self.store.create_account(
            candidate.target_id,
            name=candidate.name,
            source="imported",
            bound_workspace=None,
            auth_source=candidate.source / "auth.json",
            notes="imported from legacy codex-accounts",
        )
        return candidate.target_id

    def migrate(self, *, dry_run: bool = False, from_prefix: Optional[str] = None, from_accounts: Optional[str] = None) -> None:
        candidates = self.scan_legacy_workspaces(from_prefix)
        legacy_accounts_dir = Path(os.path.expandvars(os.path.expanduser(from_accounts))) if from_accounts else self.config.home_dir / ".codex-accounts"
        reserved_ids = {candidate.account_id for candidate in candidates if candidate.account_id}
        account_candidates = self.dedupe_legacy_account_candidates(
            self.scan_legacy_accounts(legacy_accounts_dir),
            set(reserved_ids),
        )

        if dry_run:
            self.render_migration_plan(candidates, account_candidates, legacy_accounts_dir)
            return

        self.require_external_terminal("migration")
        self.ensure_app_not_running_for_migration()
        if self.config.active_link.exists() and not self.platform.is_directory_link(self.config.active_link):
            self.fail(
                f"{self.config.active_link} 是真实目录，不能由批量迁移覆盖；请使用 codex-workspaces init <name> --migrate-current 处理当前目录。",
                f"{self.config.active_link} is a real directory and cannot be replaced by bulk migration; use codex-workspaces init <name> --migrate-current for the current directory.",
            )
        if not candidates and not account_candidates:
            self.info(self.message("没有发现可迁移的旧工作区或旧账号。", "No legacy workspaces or accounts found to migrate."))
            return
        for candidate in candidates:
            if candidate.target.exists():
                self.fail(
                    f"迁移目标已存在: {candidate.target}",
                    f"Migration target already exists: {candidate.target}",
                )

        with self.store.lock():
            self.store.ensure_layout()
            backup_dir = self.migration_backup_dir()
            self.info(self.message(f"准备迁移，备份目录: {backup_dir}", f"Preparing migration; backup directory: {backup_dir}"))
            skipped_special = self.backup_migration_sources(backup_dir, candidates, legacy_accounts_dir if account_candidates else None)
            active_target = self.current_target().path if self.current_target().kind == "target" else None
            active_migrated: Optional[LegacyWorkspaceCandidate] = None
            migrated_workspace_names: list[str] = []
            created_account_ids: list[str] = []
            for candidate in candidates:
                self.info(self.message(f"迁移工作区: {candidate.name}", f"Migrating workspace: {candidate.name}"))
                skipped_special += self.copy_supported_tree(candidate.source, candidate.target, context="migration")
                migrated_workspace_names.append(candidate.name)
                try:
                    candidate.target.chmod(0o700)
                except OSError:
                    pass
                meta = self.store.ensure_workspace_meta(candidate.name, candidate.target)
                if candidate.account_id:
                    self.info(self.message(f"创建默认账号快照: {candidate.account_id}", f"Creating default account snapshot: {candidate.account_id}"))
                    self.store.create_account(
                        candidate.account_id,
                        name=candidate.name,
                        source="workspace-default",
                        bound_workspace=candidate.name,
                        auth_source=candidate.target / "auth.json",
                        notes=f"{candidate.name} workspace default account",
                    )
                    created_account_ids.append(candidate.account_id)
                    meta.default_account_id = candidate.account_id
                    meta.active_account_id = candidate.account_id
                meta.name = candidate.name
                meta.path = str(candidate.target)
                meta.updated_at = iso_now()
                self.store.write_workspace_meta(candidate.target, meta)
                if active_target and self.same_path(active_target, candidate.source):
                    active_migrated = candidate

            imported_accounts = []
            for candidate in account_candidates:
                self.info(self.message(f"导入旧账号: {candidate.target_id}", f"Importing legacy account: {candidate.target_id}"))
                imported_accounts.append(self.import_legacy_account_candidate(candidate))
            link_target = active_migrated.target if active_migrated else (candidates[0].target if candidates and not self.config.active_link.exists() else None)
            if link_target:
                self.info(self.message(f"更新当前工作区链接: {self.config.active_link} -> {link_target}", f"Updating active workspace link: {self.config.active_link} -> {link_target}"))
                if self.platform.is_directory_link(self.config.active_link):
                    self.platform.remove_directory_link(self.config.active_link)
                self.platform.create_directory_link(link_target, self.config.active_link)
            self.info(self.message(f"迁移完成，备份目录: {backup_dir}", f"Migration complete; backup directory: {backup_dir}"))
            renamed_conflicts = [
                candidate.target_id
                for candidate in account_candidates
                if candidate.target_id != self.account_id_from_input(candidate.name)
            ]
            self.info(self.message("迁移报告:", "Migration report:"))
            self.info(self.message(f"  已迁移工作区: {len(migrated_workspace_names)}", f"  migrated workspaces: {len(migrated_workspace_names)}"))
            self.info(self.message(f"  已创建默认账号: {len(created_account_ids)}", f"  default accounts created: {len(created_account_ids)}"))
            self.info(self.message(f"  已导入旧账号: {len(imported_accounts)}", f"  legacy accounts imported: {len(imported_accounts)}"))
            self.info(self.message(f"  冲突改名账号: {len(renamed_conflicts)}", f"  renamed account conflicts: {len(renamed_conflicts)}"))
            self.info(self.message(f"  跳过特殊文件: {skipped_special}", f"  special files skipped: {skipped_special}"))

    def render_migration_plan(
        self,
        candidates: Sequence[LegacyWorkspaceCandidate],
        account_candidates: Sequence[LegacyAccountCandidate],
        legacy_accounts_dir: Path,
    ) -> None:
        self.info(self.bold(self.message("迁移计划", "Migration plan")))
        self.info(self.message("Will migrate:", "Will migrate:"))
        if candidates:
            for candidate in candidates:
                self.info(f"  {candidate.source} -> {candidate.target}")
        else:
            self.info("  -")
        self.info(self.message("Will create accounts:", "Will create accounts:"))
        account_ids = [candidate.account_id for candidate in candidates if candidate.account_id]
        if account_ids:
            for account_id in account_ids:
                self.info(f"  {account_id}")
        else:
            self.info("  -")
        self.info(self.message("Will import legacy accounts:", "Will import legacy accounts:"))
        if account_candidates:
            for candidate in account_candidates:
                self.info(f"  {candidate.source / 'auth.json'} -> {self.store.account_dir(candidate.target_id)}")
        else:
            self.info(f"  - ({legacy_accounts_dir})")
        self.info(self.message("Will backup:", "Will backup:"))
        if candidates or account_candidates or self.config.active_link.exists() or self.config.active_link.is_symlink():
            self.info(f"  {self.config.active_link}")
            for candidate in candidates:
                self.info(f"  {candidate.source}")
            if account_candidates:
                self.info(f"  {legacy_accounts_dir}")
        else:
            self.info("  -")

    def switch_workspace(self, name: str, args: Sequence[str], original_argv: Sequence[str]) -> None:
        stop_first = True
        start_after = True
        force = False
        for arg in args:
            if arg == "--no-stop":
                stop_first = False
            elif arg == "--no-start":
                start_after = False
            elif arg in {"--force", "-f"}:
                force = True
            elif arg in {"-h", "--help"}:
                self.info(usage(self.config.lang))
                return
            else:
                self.fail(f"未知参数: {arg}", f"Unknown option: {arg}")

        if self.platform.is_codex_terminal():
            if self.platform.supports_external_terminal_delegation:
                self.platform.delegate_to_external_terminal(
                    self.config,
                    self.message("切换工作区", "switch workspaces"),
                    original_argv,
                    self.stdout,
                )
                return
            self.require_external_terminal("switch")

        clean_name = strip_workspace_name(name)
        validate_workspace_name(clean_name)
        with self.store.lock():
            previous_account_id = self.save_current_live_auth()
            directory = self.workspace_dir(clean_name)
            if not directory.is_dir():
                self.fail(
                    f"工作区不存在: {directory}。可先执行: codex-workspaces init {clean_name}",
                    f"Workspace does not exist: {directory}. You can initialize it with: codex-workspaces init {clean_name}",
                )

            active = self.config.active_link
            if active.exists() and not self.platform.is_directory_link(active):
                self.fail(
                    f"{active} 已存在但不是软链接。为避免误删，请先手动备份/迁移它。",
                    f"{active} already exists but is not a symlink. Please back it up or migrate it manually before switching.",
                )

            if stop_first:
                if self.platform.supports_app_control:
                    self.stop_codex(force)
                else:
                    self.info(
                        self.message(
                            "当前平台不支持自动关闭 Codex App，继续只切换工作区链接。",
                            "App stop is not supported on this platform; continuing with the workspace link switch.",
                        )
                    )

            active.parent.mkdir(parents=True, exist_ok=True)
            if self.platform.is_directory_link(active):
                self.platform.remove_directory_link(active)
            self.platform.create_directory_link(directory, active)
            meta = self.store.ensure_workspace_meta(clean_name, directory)
            meta.last_used_at = iso_now()
            restore_account_id = self.restore_account_for_workspace_enter(meta, previous_account_id)
            if restore_account_id:
                self.restore_workspace_account(directory, meta, restore_account_id)
                meta.active_account_id = restore_account_id
            meta.updated_at = iso_now()
            self.store.write_workspace_meta(directory, meta)
            self.info(self.message(f"已切换到: {clean_name} -> {directory}", f"Switched to: {clean_name} -> {directory}"))

        if start_after:
            if self.platform.supports_app_control:
                self.start_codex()
            else:
                self.info(
                    self.message(
                        "当前平台不支持自动启动 Codex App，工作区链接已完成切换。",
                        "App start is not supported on this platform; the workspace link has been switched.",
                    )
                )

    def init_workspace(self, name: str, args: Sequence[str]) -> None:
        migrate_current = False
        for arg in args:
            if arg in {"--migrate-current", "--migrate"}:
                migrate_current = True
            elif arg in {"-h", "--help"}:
                self.info(usage(self.config.lang))
                return
            else:
                self.fail(f"未知参数: {arg}", f"Unknown option: {arg}")

        if not name:
            self.fail(
                "缺少工作区名，例如: codex-workspaces init work",
                "Missing workspace name, for example: codex-workspaces init work",
            )

        clean_name = strip_workspace_name(name)
        validate_workspace_name(clean_name)
        directory = self.workspace_dir(clean_name)
        if directory.exists():
            self.fail(f"工作区目录已存在: {directory}", f"Workspace directory already exists: {directory}")

        if migrate_current:
            self.migrate_current_workspace(clean_name, directory)
            return

        self.store.ensure_layout()
        directory.mkdir(parents=True, exist_ok=False)
        try:
            directory.chmod(0o700)
        except OSError:
            pass
        self.store.ensure_workspace_meta(clean_name, directory)
        self.info(self.message(f"已初始化工作区目录: {directory}", f"Initialized workspace directory: {directory}"))

    def migrate_current_workspace(self, clean_name: str, directory: Path) -> None:
        self.require_external_terminal("migration")
        self.ensure_app_not_running_for_migration()
        active = self.config.active_link
        if not active.exists() or self.platform.is_directory_link(active):
            self.fail(
                f"{active} 不是可迁移的真实目录。",
                f"{active} is not a real directory that can be migrated.",
            )
        if not active.is_dir():
            self.fail(f"{active} 不是目录。", f"{active} is not a directory.")

        with self.store.lock():
            self.store.ensure_layout()
            backup_dir = self.migration_backup_dir()
            self.copy_path_for_backup(active, backup_dir / "codex")
            shutil.move(str(active), str(directory))
            try:
                directory.chmod(0o700)
            except OSError:
                pass
            meta = self.store.ensure_workspace_meta(clean_name, directory)
            auth_path = directory / "auth.json"
            if auth_path.is_file():
                account_id = self.unique_account_id("acct_" + clean_name, directory)
                self.store.create_account(
                    account_id,
                    name=clean_name,
                    source="workspace-default",
                    bound_workspace=clean_name,
                    auth_source=auth_path,
                    notes=f"{clean_name} workspace default account",
                )
                meta.default_account_id = account_id
                meta.active_account_id = account_id
            meta.name = clean_name
            meta.path = str(directory)
            meta.updated_at = iso_now()
            meta.last_used_at = iso_now()
            self.store.write_workspace_meta(directory, meta)
            self.platform.create_directory_link(directory, active)
        self.info(self.message(f"已迁移当前工作区: {clean_name} -> {directory}", f"Migrated current workspace: {clean_name} -> {directory}"))

    def rename_workspace(self, old_name: str, new_name: str) -> None:
        old_clean = strip_workspace_name(old_name)
        new_clean = strip_workspace_name(new_name)
        validate_workspace_name(old_clean)
        validate_workspace_name(new_clean)
        old_directory = self.workspace_dir(old_clean)
        new_directory = self.workspace_dir(new_clean)

        if not old_directory.is_dir():
            self.fail(f"工作区不存在: {old_directory}", f"Workspace does not exist: {old_directory}")
        if new_directory.exists():
            self.fail(f"目标工作区已存在: {new_directory}", f"Target workspace already exists: {new_directory}")

        current = self.current_target()
        was_current = current.kind == "target" and current.path is not None and self.same_path(
            self.real_dir(old_directory),
            current.path,
        )

        old_directory.rename(new_directory)
        if was_current and self.platform.is_directory_link(self.config.active_link):
            self.platform.remove_directory_link(self.config.active_link)
            self.platform.create_directory_link(new_directory, self.config.active_link)
        meta = self.store.ensure_workspace_meta(old_clean, new_directory)
        meta.name = new_clean
        meta.path = str(new_directory)
        meta.updated_at = iso_now()
        self.store.write_workspace_meta(new_directory, meta)

        self.info(
            self.message(
                f"已重命名工作区: {old_clean} -> {new_clean}",
                f"Renamed workspace: {old_clean} -> {new_clean}",
            )
        )

    def delete_workspace(self, name: str, args: Sequence[str]) -> None:
        force = False
        for arg in args:
            if arg == "--force":
                force = True
            else:
                self.fail(f"未知参数: {arg}", f"Unknown option: {arg}")
        if not force:
            self.fail(
                "删除工作区需要 --force，避免误删。",
                "Deleting a workspace requires --force to avoid accidental data loss.",
            )

        clean_name = strip_workspace_name(name)
        validate_workspace_name(clean_name)
        directory = self.workspace_dir(clean_name)
        if not directory.is_dir():
            self.fail(f"工作区不存在: {directory}", f"Workspace does not exist: {directory}")

        current = self.current_target()
        if current.kind == "target" and current.path is not None and self.same_path(self.real_dir(directory), current.path):
            self.fail(
                "不能删除当前正在使用的工作区；请先切换到其他工作区。",
                "Cannot delete the active workspace; switch to another workspace first.",
            )

        shutil.rmtree(directory)
        self.info(self.message(f"已删除工作区: {clean_name}", f"Deleted workspace: {clean_name}"))

    def restore_account_for_workspace_enter(self, meta: WorkspaceMeta, previous_account_id: Optional[str]) -> Optional[str]:
        policy = self.config.restore_policy
        if policy == "keep-current":
            if previous_account_id and self.store.account_auth_path(previous_account_id).is_file():
                return previous_account_id
            return meta.active_account_id if meta.active_account_id and self.store.account_auth_path(meta.active_account_id).is_file() else None
        if policy == "last-active":
            if meta.active_account_id and self.store.account_auth_path(meta.active_account_id).is_file():
                return meta.active_account_id
            return meta.default_account_id if meta.default_account_id and self.store.account_auth_path(meta.default_account_id).is_file() else None
        if meta.restore_default_on_enter and meta.default_account_id:
            return meta.default_account_id
        return None

    def save_current_live_auth(self) -> Optional[str]:
        current = self.current_target()
        if current.kind != "target" or current.path is None:
            return None
        name = self.current_name(current.path)
        if not name:
            return None
        meta = self.store.ensure_workspace_meta(name, current.path)
        if not meta.active_account_id:
            return None
        auth_path = current.path / "auth.json"
        if auth_path.is_file() and self.store.account_meta_path(meta.active_account_id).is_file():
            self.store.save_auth_to_account(meta.active_account_id, auth_path)
        return meta.active_account_id

    def restore_workspace_account(self, directory: Path, meta: WorkspaceMeta, account_id: str) -> None:
        if not self.store.account_auth_path(account_id).is_file():
            self.fail(
                f"账号不存在或缺少 auth.json: {account_id}",
                f"Account not found or missing auth.json: {account_id}\nHint: run `codex-workspaces accounts list`",
            )
        copy_auth(self.store.account_auth_path(account_id), directory / "auth.json")
        self.store.touch_account_used(account_id)

    def account_id_from_input(self, value: str) -> str:
        clean = strip_workspace_name(value)
        if clean.startswith("acct_"):
            validate_workspace_name(clean)
            validate_workspace_name(clean[len("acct_") :])
            return clean
        validate_workspace_name(clean)
        return "acct_" + clean

    def account_name_from_input(self, value: str) -> str:
        clean = strip_workspace_name(value)
        if clean.startswith("acct_"):
            clean = clean[len("acct_") :]
        validate_workspace_name(clean)
        return clean

    def accounts_list(self) -> None:
        self.store.ensure_layout()
        current_account = None
        default_account = None
        current = self.current_target()
        if current.kind == "target" and current.path is not None:
            name = self.current_name(current.path)
            if name:
                meta = self.store.ensure_workspace_meta(name, current.path)
                current_account = meta.active_account_id
                default_account = meta.default_account_id
        self.info(self.bold(f"Accounts: {self.config.accounts_dir}"))
        accounts = self.store.list_accounts()
        if not accounts:
            self.info(self.message("未找到账号。", "No accounts found."))
            return
        self.info("CURRENT  DEFAULT  AUTH  STATUS       ACCOUNT          SOURCE              DEFAULT_IN        ACTIVE_IN         NOTE                     LAST_USED")
        for account in accounts:
            current_mark = "*" if account.id == current_account else ""
            default_mark = "*" if account.id == default_account else ""
            default_refs, active_refs = self.workspace_account_references(account.id)
            status = self.account_reference_status(default_refs, active_refs)
            self.info(
                f"{current_mark:<8} {default_mark:<8} {self._yes_no(self.store.account_auth_path(account.id).is_file()):<5} "
                f"{status:<12} {account.id:<16} {account.source:<19} "
                f"{self.format_refs(default_refs):<17} {self.format_refs(active_refs):<17} "
                f"{self.format_note(account.notes) or '-':<24} {account.last_used_at or '-'}"
            )

    def accounts_current(self) -> None:
        current = self.current_target()
        if current.kind != "target" or current.path is None:
            self.fail("当前工作区不存在。", "Current workspace does not exist.")
        name = self.current_name(current.path) or "current"
        meta = self.store.ensure_workspace_meta(name, current.path)
        self.info(f"{name}: active={meta.active_account_id or '-'} default={meta.default_account_id or '-'}")

    def accounts_info(self, account: str) -> None:
        account_id = self.account_id_from_input(account)
        if not self.store.account_meta_path(account_id).is_file():
            self.fail(f"账号不存在: {account_id}", f"Account not found: {account_id}\nHint: run `codex-workspaces accounts list`")
        meta = self.store.read_account_meta(account_id)
        current_account = None
        default_account = None
        current_workspace = None
        current = self.current_target()
        if current.kind == "target" and current.path is not None:
            current_workspace = self.current_name(current.path)
            if current_workspace:
                workspace_meta = self.store.ensure_workspace_meta(current_workspace, current.path)
                current_account = workspace_meta.active_account_id
                default_account = workspace_meta.default_account_id
        default_refs, active_refs = self.workspace_account_references(account_id)
        self.info(self.bold(self.message(f"账号: {account_id}", f"Account: {account_id}")))
        self.info(f"current: {self._yes_no(account_id == current_account)}")
        self.info(f"default_for_current_workspace: {self._yes_no(account_id == default_account)}")
        self.info(f"current_workspace: {current_workspace or '-'}")
        self.info(f"auth_exists: {self._yes_no(self.store.account_auth_path(account_id).is_file())}")
        self.info(f"orphan: {self._yes_no(not default_refs and not active_refs)}")
        self.info(f"default_workspaces: {self.format_refs(default_refs)}")
        self.info(f"active_workspaces: {self.format_refs(active_refs)}")
        self.info(f"path: {self.store.account_dir(account_id)}")
        self.info(f"auth_path: {self.store.account_auth_path(account_id)}")
        self.info(f"meta_path: {self.store.account_meta_path(account_id)}")
        for key, value in meta.to_dict().items():
            self.info(f"{key}: {value if value is not None else '-'}")

    def account_reference_status(self, default_refs: Sequence[str], active_refs: Sequence[str]) -> str:
        if not default_refs and not active_refs:
            return "orphan"
        if active_refs and not default_refs:
            return "active-only"
        if default_refs and active_refs:
            return "default+active"
        return "default"

    def format_refs(self, refs: Sequence[str]) -> str:
        return ", ".join(refs) if refs else "-"

    def accounts_init(self, account: str) -> None:
        self.store.ensure_layout()
        account_id = self.account_id_from_input(account)
        name = self.account_name_from_input(account)
        with self.store.lock():
            self.store.create_account(
                account_id,
                name=name,
                source="standalone",
                bound_workspace=None,
                auth_source=None,
                notes="",
            )
        self.info(self.message(f"已初始化账号: {account_id}", f"Initialized account: {account_id}"))

    def accounts_save(self, account: str) -> None:
        self.store.ensure_layout()
        account_id = self.account_id_from_input(account)
        current = self.current_target()
        if current.kind != "target" or current.path is None:
            self.fail("当前工作区不存在。", "Current workspace does not exist.")
        with self.store.lock():
            if not self.store.account_meta_path(account_id).is_file():
                self.store.create_account(
                    account_id,
                    name=self.account_name_from_input(account),
                    source="manual",
                    bound_workspace=None,
                    auth_source=None,
                    notes="",
                )
            self.store.save_auth_to_account(account_id, current.path / "auth.json")
        self.info(self.message(f"已保存账号: {account_id}", f"Saved account: {account_id}"))

    def accounts_add(self, account: str, args: Sequence[str]) -> None:
        login = False
        stop_first = True
        start_after = True
        force = False
        keep_temp = False
        timeout = 300
        index = 0
        while index < len(args):
            arg = args[index]
            if arg == "--login":
                login = True
            elif arg == "--no-stop":
                stop_first = False
            elif arg == "--no-start":
                start_after = False
            elif arg in {"--force", "-f"}:
                force = True
            elif arg == "--keep-temp":
                keep_temp = True
            elif arg == "--timeout":
                index += 1
                if index >= len(args):
                    self.fail("缺少 --timeout 数值", "Missing value for --timeout")
                timeout = self.parse_non_negative_int(args[index], "--timeout")
            elif arg.startswith("--timeout="):
                timeout = self.parse_non_negative_int(arg.split("=", 1)[1], "--timeout")
            else:
                self.fail(f"未知参数: {arg}", f"Unknown option: {arg}")
            index += 1

        if not login:
            self.fail(
                "用法: codex-workspaces accounts add <账号> --login",
                "Usage: codex-workspaces accounts add <account> --login",
            )
        if self.platform.is_codex_terminal():
            self.require_external_terminal("account login")

        self.store.ensure_layout()
        account_id = self.account_id_from_input(account)
        account_name = self.account_name_from_input(account)
        if self.store.account_dir(account_id).exists():
            self.fail(f"账号已存在: {account_id}", f"Account already exists: {account_id}")

        temp_name = self.login_temp_workspace_name(account_name)
        temp_dir = self.workspace_dir(temp_name)
        previous = self.current_target()
        previous_path = previous.path if previous.kind == "target" else None
        previous_name = self.current_name(previous_path) if previous_path is not None else None
        if self.config.active_link.exists() and not self.platform.is_directory_link(self.config.active_link):
            self.fail(
                f"{self.config.active_link} 已存在但不是软链接。为避免误删，请先手动备份/迁移它。",
                f"{self.config.active_link} already exists but is not a symlink. Please back it up or migrate it manually first.",
            )

        if stop_first:
            if self.platform.supports_app_control:
                self.stop_codex(force)
            else:
                self.info(self.message("当前平台不支持自动关闭 Codex App。", "App stop is not supported on this platform."))

        with self.store.lock():
            previous_account_id = self.save_current_live_auth()
            if temp_dir.exists():
                self.fail(
                    f"临时登录工作区已存在: {temp_dir}",
                    f"Login temporary workspace already exists: {temp_dir}\nHint: run `codex-workspaces accounts cleanup-login-temp`.",
                )
            temp_dir.mkdir(parents=True, exist_ok=False)
            try:
                temp_dir.chmod(0o700)
            except OSError:
                pass
            temp_meta = self.store.ensure_workspace_meta(temp_name, temp_dir)
            temp_meta.last_used_at = iso_now()
            temp_meta.updated_at = iso_now()
            self.store.write_workspace_meta(temp_dir, temp_meta)
            self.activate_workspace_link(temp_dir)

        self.info(
            self.message(
                f"已切换到临时登录工作区: {temp_name}",
                f"Switched to login temporary workspace: {temp_name}",
            )
        )
        self.info(
            self.message(
                "请在 Codex 中登录新账号。登录完成并生成 auth.json 后会保存账号并恢复原工作区。",
                "Log in to the new account in Codex. After auth.json appears, the account will be saved and the previous workspace restored.",
            )
        )

        if start_after:
            if self.platform.supports_app_control:
                self.start_codex()
            else:
                self.info(self.message("当前平台不支持自动启动 Codex App，请手动启动并登录。", "App start is not supported on this platform; start Codex manually and log in."))

        self.wait_for_login_auth(temp_dir, timeout)

        if stop_first:
            if self.platform.supports_app_control:
                self.stop_codex(force)
            else:
                self.info(self.message("当前平台不支持自动关闭 Codex App，继续恢复工作区链接。", "App stop is not supported on this platform; restoring the workspace link."))

        with self.store.lock():
            self.store.create_account(
                account_id,
                name=account_name,
                source="login-temp",
                bound_workspace=None,
                auth_source=temp_dir / "auth.json",
                notes="created by accounts add --login",
            )
            self.restore_after_login_temp(previous_path, previous_name, previous_account_id)
            if not keep_temp:
                shutil.rmtree(temp_dir, ignore_errors=True)

        if start_after:
            if self.platform.supports_app_control:
                self.start_codex()
            else:
                self.info(self.message("当前平台不支持自动启动 Codex App，工作区链接已恢复。", "App start is not supported on this platform; the workspace link has been restored."))
        self.info(self.message(f"已新增账号: {account_id}", f"Added account: {account_id}"))

    def login_temp_workspace_name(self, account_name: str) -> str:
        base = f"login-{account_name}"
        if len(base) <= 64:
            return base
        digest = hashlib.sha1(account_name.encode("utf-8")).hexdigest()[:8]
        return f"login-{account_name[:49]}-{digest}"

    def activate_workspace_link(self, directory: Path) -> None:
        self.config.active_link.parent.mkdir(parents=True, exist_ok=True)
        if self.platform.is_directory_link(self.config.active_link):
            self.platform.remove_directory_link(self.config.active_link)
        self.platform.create_directory_link(directory, self.config.active_link)

    def wait_for_login_auth(self, directory: Path, timeout: int) -> None:
        auth_path = directory / "auth.json"
        if auth_path.is_file():
            return
        if getattr(self.stdin, "isatty", lambda: False)():
            self.info(self.message("登录完成后按 Enter 继续。", "Press Enter after login completes."))
            self.stdin.readline()
            if auth_path.is_file():
                return
        if timeout > 0:
            deadline = time.monotonic() + timeout
            self.info(self.message(f"等待 auth.json，最多 {timeout} 秒。", f"Waiting for auth.json for up to {timeout}s."))
            while time.monotonic() < deadline:
                if auth_path.is_file():
                    return
                time.sleep(1)
        if auth_path.is_file():
            return
        self.fail(
            f"未发现登录生成的 auth.json: {auth_path}",
            f"Login auth.json was not created: {auth_path}\nHint: finish login, run `codex-workspaces accounts save {strip_workspace_name(str(directory)).removeprefix('login-')}`, or clean up with `codex-workspaces accounts cleanup-login-temp`.",
        )

    def restore_after_login_temp(
        self,
        previous_path: Optional[Path],
        previous_name: Optional[str],
        previous_account_id: Optional[str],
    ) -> None:
        if self.platform.is_directory_link(self.config.active_link):
            self.platform.remove_directory_link(self.config.active_link)
        if previous_path is None:
            return
        self.platform.create_directory_link(previous_path, self.config.active_link)
        if not previous_name:
            return
        meta = self.store.ensure_workspace_meta(previous_name, previous_path)
        restore_account_id = previous_account_id or meta.active_account_id or meta.default_account_id
        if restore_account_id and self.store.account_auth_path(restore_account_id).is_file():
            self.restore_workspace_account(previous_path, meta, restore_account_id)
            meta.active_account_id = restore_account_id
        meta.last_used_at = iso_now()
        meta.updated_at = iso_now()
        self.store.write_workspace_meta(previous_path, meta)

    def parse_non_negative_int(self, value: str, option: str) -> int:
        try:
            parsed = int(value)
        except ValueError:
            self.fail(f"{option} 必须是非负整数", f"{option} must be a non-negative integer")
        if parsed < 0:
            self.fail(f"{option} 必须是非负整数", f"{option} must be a non-negative integer")
        return parsed

    def accounts_cleanup_login_temp(self, args: Sequence[str]) -> None:
        for arg in args:
            if arg not in {"--force"}:
                self.fail(f"未知参数: {arg}", f"Unknown option: {arg}")
        self.store.ensure_layout()
        current = self.current_target()
        removed: list[str] = []
        skipped_active: list[str] = []
        with self.store.lock():
            for directory in self.workspace_dirs():
                name = strip_workspace_name(str(directory))
                if not name.startswith("login-"):
                    continue
                if current.kind == "target" and current.path is not None and self.same_path(self.real_dir(directory), current.path):
                    skipped_active.append(name)
                    continue
                shutil.rmtree(directory)
                removed.append(name)
        self.info(self.message(f"已清理临时登录工作区: {len(removed)}", f"Cleaned login temporary workspaces: {len(removed)}"))
        for name in removed:
            self.info(f"  {name}")
        if skipped_active:
            self.info(self.message(f"跳过当前临时工作区: {', '.join(skipped_active)}", f"Skipped active temporary workspace: {', '.join(skipped_active)}"))

    def accounts_use(self, account: str) -> None:
        self.store.ensure_layout()
        account_id = self.account_id_from_input(account)
        if not self.store.account_auth_path(account_id).is_file():
            self.fail(f"账号不存在: {account_id}", f"Account not found: {account_id}\nHint: run `codex-workspaces accounts list`")
        current = self.current_target()
        if current.kind != "target" or current.path is None:
            self.fail("当前工作区不存在。", "Current workspace does not exist.")
        name = self.current_name(current.path) or "current"
        with self.store.lock():
            meta = self.store.ensure_workspace_meta(name, current.path)
            if meta.active_account_id:
                auth_path = current.path / "auth.json"
                if auth_path.is_file() and self.store.account_meta_path(meta.active_account_id).is_file():
                    self.store.save_auth_to_account(meta.active_account_id, auth_path)
            copy_auth(self.store.account_auth_path(account_id), current.path / "auth.json")
            meta.active_account_id = account_id
            meta.updated_at = iso_now()
            self.store.write_workspace_meta(current.path, meta)
            self.store.touch_account_used(account_id)
        self.info(self.message(f"已切换当前工作区账号: {account_id}", f"Switched current workspace account: {account_id}"))

    def accounts_restore_default(self, workspace: Optional[str] = None) -> None:
        self.store.ensure_layout()
        if workspace:
            clean_name = strip_workspace_name(workspace)
            validate_workspace_name(clean_name)
            directory = self.workspace_dir(clean_name)
            if not directory.is_dir():
                self.fail(f"工作区不存在: {directory}", f"Workspace does not exist: {directory}")
        else:
            current = self.current_target()
            if current.kind != "target" or current.path is None:
                self.fail("当前工作区不存在。", "Current workspace does not exist.")
            directory = current.path
            clean_name = self.current_name(directory) or "current"

        with self.store.lock():
            meta = self.store.ensure_workspace_meta(clean_name, directory)
            if not meta.default_account_id:
                self.fail(
                    f"工作区没有默认账号: {clean_name}",
                    f"Workspace has no default account: {clean_name}\nHint: run `codex-workspaces accounts set-default {clean_name} <account>`",
                )
            if meta.active_account_id:
                auth_path = directory / "auth.json"
                if auth_path.is_file() and self.store.account_meta_path(meta.active_account_id).is_file():
                    self.store.save_auth_to_account(meta.active_account_id, auth_path)
            self.restore_workspace_account(directory, meta, meta.default_account_id)
            meta.active_account_id = meta.default_account_id
            meta.updated_at = iso_now()
            self.store.write_workspace_meta(directory, meta)
        self.info(self.message(f"已恢复默认账号: {meta.default_account_id}", f"Restored default account: {meta.default_account_id}"))

    def accounts_set_default(self, workspace: str, account: str, activate: bool = False) -> None:
        self.store.ensure_layout()
        clean_name = strip_workspace_name(workspace)
        validate_workspace_name(clean_name)
        account_id = self.account_id_from_input(account)
        directory = self.workspace_dir(clean_name)
        if not directory.is_dir():
            self.fail(f"工作区不存在: {directory}", f"Workspace does not exist: {directory}")
        if not self.store.account_auth_path(account_id).is_file():
            self.fail(f"账号不存在: {account_id}", f"Account not found: {account_id}\nHint: run `codex-workspaces accounts list`")
        with self.store.lock():
            meta = self.store.ensure_workspace_meta(clean_name, directory)
            meta.default_account_id = account_id
            if activate:
                self.restore_workspace_account(directory, meta, account_id)
                meta.active_account_id = account_id
            meta.updated_at = iso_now()
            self.store.write_workspace_meta(directory, meta)
            account_meta = self.store.read_account_meta(account_id)
            account_meta.source = "workspace-default"
            account_meta.bound_workspace = clean_name
            account_meta.updated_at = iso_now()
            self.store.write_account_meta(account_meta)
        self.info(self.message(f"已设置默认账号: {clean_name} -> {account_id}", f"Set default account: {clean_name} -> {account_id}"))

    def workspace_account_references(self, account_id: str) -> tuple[list[str], list[str]]:
        default_refs: list[str] = []
        active_refs: list[str] = []
        for directory in self.workspace_dirs():
            name = strip_workspace_name(str(directory))
            meta = self.store.ensure_workspace_meta(name, directory)
            if meta.default_account_id == account_id:
                default_refs.append(name)
            if meta.active_account_id == account_id:
                active_refs.append(name)
        return default_refs, active_refs

    def accounts_rename(self, old_account: str, new_account: str) -> None:
        self.store.ensure_layout()
        old_id = self.account_id_from_input(old_account)
        new_id = self.account_id_from_input(new_account)
        if old_id == new_id:
            self.fail("新旧账号名相同。", "Old and new account names are the same.")
        old_dir = self.store.account_dir(old_id)
        new_dir = self.store.account_dir(new_id)
        if not self.store.account_meta_path(old_id).is_file():
            self.fail(f"账号不存在: {old_id}", f"Account not found: {old_id}\nHint: run `codex-workspaces accounts list`")
        if new_dir.exists():
            self.fail(f"目标账号已存在: {new_id}", f"Target account already exists: {new_id}")

        with self.store.lock():
            old_dir.rename(new_dir)
            meta = self.store.read_account_meta(new_id)
            meta.id = new_id
            meta.name = self.account_name_from_input(new_account)
            meta.updated_at = iso_now()
            self.store.write_account_meta(meta)
            old_meta_path = self.store.account_meta_path(old_id)
            if old_meta_path.exists():
                old_meta_path.unlink()

            for directory in self.workspace_dirs():
                name = strip_workspace_name(str(directory))
                workspace_meta = self.store.ensure_workspace_meta(name, directory)
                changed = False
                if workspace_meta.default_account_id == old_id:
                    workspace_meta.default_account_id = new_id
                    changed = True
                if workspace_meta.active_account_id == old_id:
                    workspace_meta.active_account_id = new_id
                    changed = True
                if changed:
                    workspace_meta.updated_at = iso_now()
                    self.store.write_workspace_meta(directory, workspace_meta)
        self.info(self.message(f"已重命名账号: {old_id} -> {new_id}", f"Renamed account: {old_id} -> {new_id}"))

    def accounts_delete(self, account: str, args: Sequence[str]) -> None:
        self.store.ensure_layout()
        force = False
        for arg in args:
            if arg == "--force":
                force = True
            else:
                self.fail(f"未知参数: {arg}", f"Unknown option: {arg}")
        if not force:
            self.fail(
                "删除账号需要 --force，避免误删认证快照。",
                "Deleting an account requires --force to avoid accidental credential loss.",
            )

        account_id = self.account_id_from_input(account)
        account_dir = self.store.account_dir(account_id)
        if not self.store.account_meta_path(account_id).is_file():
            self.fail(f"账号不存在: {account_id}", f"Account not found: {account_id}\nHint: run `codex-workspaces accounts list`")

        default_refs, active_refs = self.workspace_account_references(account_id)
        if default_refs:
            refs = ", ".join(default_refs)
            self.fail(
                f"不能删除默认账号 {account_id}，仍被工作区使用: {refs}",
                f"Cannot delete default account {account_id}; still used by workspaces: {refs}\nHint: run `codex-workspaces accounts set-default <workspace> <account>` first.",
            )

        with self.store.lock():
            shutil.rmtree(account_dir)
            for directory in self.workspace_dirs():
                name = strip_workspace_name(str(directory))
                workspace_meta = self.store.ensure_workspace_meta(name, directory)
                if workspace_meta.active_account_id == account_id:
                    workspace_meta.active_account_id = None
                    workspace_meta.updated_at = iso_now()
                    self.store.write_workspace_meta(directory, workspace_meta)
        suffix = f" ({', '.join(active_refs)})" if active_refs else ""
        self.info(self.message(f"已删除账号: {account_id}{suffix}", f"Deleted account: {account_id}{suffix}"))

    def accounts_note(self, account: str, args: Sequence[str]) -> None:
        self.store.ensure_layout()
        account_id = self.account_id_from_input(account)
        if not self.store.account_meta_path(account_id).is_file():
            self.fail(f"账号不存在: {account_id}", f"Account not found: {account_id}\nHint: run `codex-workspaces accounts list`")
        meta = self.store.read_account_meta(account_id)
        if not args:
            if meta.notes:
                self.info(meta.notes)
            else:
                self.info(self.message("未设置备注。", "No note set."))
            return
        if len(args) == 1 and args[0] == "--clear":
            meta.notes = ""
            meta.updated_at = iso_now()
            self.store.write_account_meta(meta)
            self.info(self.message(f"已清除账号备注: {account_id}", f"Cleared account note: {account_id}"))
            return
        text = " ".join(args).strip()
        if not text:
            self.fail("备注不能为空。", "Note cannot be empty.")
        meta.notes = text
        meta.updated_at = iso_now()
        self.store.write_account_meta(meta)
        self.info(self.message(f"已更新账号备注: {account_id}", f"Updated account note: {account_id}"))

    def accounts_import_workspaces(self) -> None:
        self.store.ensure_layout()
        imported: list[str] = []
        with self.store.lock():
            for directory in self.workspace_dirs():
                name = strip_workspace_name(str(directory))
                try:
                    validate_workspace_name(name)
                except CodexWorkspacesError:
                    continue
                auth_path = directory / "auth.json"
                if not auth_path.is_file():
                    continue
                meta = self.store.ensure_workspace_meta(name, directory)
                if meta.default_account_id and self.store.account_auth_path(meta.default_account_id).is_file():
                    continue
                account_id = self.unique_account_id("acct_" + name, directory)
                self.store.create_account(
                    account_id,
                    name=name,
                    source="workspace-default",
                    bound_workspace=name,
                    auth_source=auth_path,
                    notes=f"{name} workspace default account",
                )
                meta.default_account_id = account_id
                meta.active_account_id = account_id
                meta.updated_at = iso_now()
                self.store.write_workspace_meta(directory, meta)
                imported.append(account_id)
        self.info(self.message(f"已导入工作区默认账号: {len(imported)}", f"Imported workspace default accounts: {len(imported)}"))
        for account_id in imported:
            self.info(f"  {account_id}")

    def accounts_import_legacy(self, legacy_accounts_dir: str) -> None:
        self.store.ensure_layout()
        source_dir = Path(os.path.expandvars(os.path.expanduser(legacy_accounts_dir)))
        candidates = self.scan_legacy_accounts(source_dir)
        if not candidates:
            self.info(self.message("没有发现可导入的旧账号。", "No legacy accounts found to import."))
            return
        imported: list[str] = []
        with self.store.lock():
            for candidate in candidates:
                imported.append(self.import_legacy_account_candidate(candidate))
        self.info(self.message(f"已导入旧账号: {len(imported)}", f"Imported legacy accounts: {len(imported)}"))
        for account_id in imported:
            self.info(f"  {account_id}")

    def note_workspace(self, name: str, args: Sequence[str]) -> None:
        clean_name = strip_workspace_name(name)
        validate_workspace_name(clean_name)
        directory = self.workspace_dir(clean_name)
        if not directory.is_dir():
            self.fail(f"工作区不存在: {directory}", f"Workspace does not exist: {directory}")

        note_file = self.note_path(directory)
        if not args:
            note = self.workspace_note(directory)
            if note:
                self.info(note)
            else:
                self.info(self.message("未设置备注。", "No note set."))
            return

        if len(args) == 1 and args[0] == "--clear":
            note_file.unlink(missing_ok=True)
            self.info(self.message(f"已清除备注: {clean_name}", f"Cleared note: {clean_name}"))
            return

        text = " ".join(args).strip()
        if not text:
            self.fail("备注不能为空。", "Note cannot be empty.")
        note_file.write_text(text + "\n", encoding="utf-8")
        try:
            note_file.chmod(0o600)
        except OSError:
            pass
        self.info(self.message(f"已更新备注: {clean_name}", f"Updated note: {clean_name}"))

    def install_self(self, destination: Optional[str] = None) -> None:
        dest = Path(destination) if destination else self.default_install_dir()
        if dest is None:
            self.fail(
                "无法判断安装目录，请指定，例如: codex-workspaces install /usr/local/bin",
                "Could not choose an install directory. Please specify one, for example: codex-workspaces install /usr/local/bin",
            )
        dest.mkdir(parents=True, exist_ok=True)
        if self.platform.is_windows:
            launcher = dest / "codex-workspaces.cmd"
            launcher.write_text(
                f'@echo off\r\n"{sys.executable}" -m codex_workspaces %*\r\n',
                encoding="utf-8",
            )
        else:
            launcher = dest / "codex-workspaces"
            launcher.write_text(
                "#!/usr/bin/env sh\n"
                f"exec {shlex.quote(sys.executable)} -m codex_workspaces \"$@\"\n",
                encoding="utf-8",
            )
            launcher.chmod(0o755)
        self.info(self.message(f"已安装: {launcher}", f"Installed: {launcher}"))

        if not self.path_contains_dir(dest):
            self.info(
                self.message(
                    f"提醒: {dest} 目前不在 PATH 中，需要加入 shell 配置。",
                    f"Note: {dest} is not currently in PATH. Add it to your shell config before using the command directly.",
                )
            )

    def path_contains_dir(self, directory: Path) -> bool:
        path_parts = os.environ.get("PATH", "").split(os.pathsep)
        target = os.path.normcase(os.path.abspath(directory))
        return any(os.path.normcase(os.path.abspath(part or ".")) == target for part in path_parts)

    def default_install_dir(self) -> Optional[Path]:
        candidates: Iterable[Path]
        if self.platform.is_windows:
            candidates = [
                self.config.home_dir / "AppData" / "Roaming" / "Python" / "Scripts",
                self.config.home_dir / ".local" / "bin",
            ]
        else:
            candidates = [
                self.config.home_dir / ".local" / "bin",
                self.config.home_dir / "bin",
                Path("/opt/homebrew/bin"),
                Path("/usr/local/bin"),
            ]
        for directory in candidates:
            if self.path_contains_dir(directory) and directory.is_dir() and os.access(directory, os.W_OK):
                return directory
        for directory in candidates:
            if self.path_contains_dir(directory) or directory == self.config.home_dir / ".local" / "bin":
                return directory
        return None


def usage(lang: str) -> str:
    if lang == "zh":
        return """codex-workspaces - Codex 多工作区切换工具

工作区约定:
  当前工作区: ~/.codex                 软链接/目录链接
  管理根目录: ~/.codex-workspaces
  工作区目录: ~/.codex-workspaces/workspaces/<工作区名>
  账号目录:   ~/.codex-workspaces/accounts/<账号>

用法:
  codex-workspaces list | ls
      查看所有工作区目录，并标出当前工作区。

  codex-workspaces current
      显示当前 ~/.codex 指向哪个工作区。

  codex-workspaces info <工作区名>
      查看单个工作区的路径、大小、备注、默认账号、当前账号和最近使用时间。

  codex-workspaces doctor
      输出路径、平台、App 控制、当前工作区和账号状态诊断。

  codex-workspaces stats [工作区名] [--days 天数]
      只读本地 Codex state_*.sqlite，统计 token 用量。

  codex-workspaces use <工作区名> [--no-stop] [--no-start] [--force]
  codex-workspaces switch <工作区名> [--no-stop] [--no-start] [--force]
  codex-workspaces <工作区名>
      切换 ~/.codex 链接到指定工作区目录。
      macOS 上默认会关闭 Codex App、切换工作区、再启动 Codex App。
      Linux/Windows 上会跳过 App 启停，只切换工作区链接。

  codex-workspaces stop [--force]
      关闭 Codex App。当前仅支持 macOS。

  codex-workspaces start
      启动 Codex App。当前仅支持 macOS。

  codex-workspaces restart [--force]
      重启 Codex App。当前仅支持 macOS。

  codex-workspaces init <工作区名> [--migrate-current]
      初始化新的工作区目录 ~/.codex-workspaces/workspaces/<工作区名>。
      --migrate-current 会把当前真实 ~/.codex 目录迁移成该工作区。

  codex-workspaces migrate [--dry-run] [--from-prefix 路径] [--from-accounts 路径]
      迁移旧 ~/.codex-<工作区名> 目录，并可导入旧 ~/.codex-accounts。

  codex-workspaces accounts list
  codex-workspaces accounts current
  codex-workspaces accounts info <账号>
  codex-workspaces accounts init <账号>
  codex-workspaces accounts save <账号>
  codex-workspaces accounts add <账号> --login [--timeout 秒] [--keep-temp]
  codex-workspaces accounts login-temp <账号>
  codex-workspaces accounts use <账号>
  codex-workspaces accounts restore-default [工作区]
  codex-workspaces accounts set-default <工作区> <账号> [--activate]
  codex-workspaces accounts rename <旧账号> <新账号>
  codex-workspaces accounts delete <账号> --force
  codex-workspaces accounts note <账号> [备注文本|--clear]
  codex-workspaces accounts cleanup-login-temp
  codex-workspaces accounts import-workspaces
  codex-workspaces accounts import-legacy <旧账号目录>
      管理 auth.json 账号快照。accounts add --login 会创建临时登录工作区并在登录完成后保存账号。

  codex-workspaces rename <旧工作区名> <新工作区名>
      重命名工作区；如果重命名当前工作区，会同步更新当前链接。

  codex-workspaces delete <工作区名> --force
      删除工作区目录。不能删除当前正在使用的工作区。

  codex-workspaces note <工作区名> [备注文本|--clear]
      查看、设置或清除工作区备注。

  codex-workspaces install [目录]
      安装 Python 启动器到 PATH 目录。推荐优先使用 pipx 或 pip 安装。

  codex-workspaces help
      显示帮助。

环境变量:
  CODEX_APP_NAME        App 名称，默认 Codex
  CODEX_QUIT_TIMEOUT    等待 App 退出秒数，默认 20
  CODEX_WORKSPACES_LINK   当前工作区链接，默认 ~/.codex
  CODEX_WORKSPACES_ROOT   管理根目录，默认 ~/.codex-workspaces
  CODEX_WORKSPACES_RESTORE_POLICY  进入工作区时账号恢复策略: workspace-default、last-active、keep-current
  CODEX_WORKSPACES_LANG   强制提示语言，可设为 zh 或 en"""

    return """codex-workspaces - Codex multi-workspace switcher

Workspace layout:
  Active workspace: ~/.codex                 symlink/directory link
  Managed root:     ~/.codex-workspaces
  Workspace dirs:   ~/.codex-workspaces/workspaces/<workspace>
  Account dirs:     ~/.codex-workspaces/accounts/<account>

Usage:
  codex-workspaces list | ls
      List all workspace directories and mark the active workspace.

  codex-workspaces current
      Show where ~/.codex currently points.

  codex-workspaces info <workspace>
      Show one workspace's path, size, note, default account, active account, and last-used time.

  codex-workspaces doctor
      Print path, platform, app-control, current workspace, and account diagnostics.

  codex-workspaces stats [workspace] [--days days]
      Read local Codex state_*.sqlite in read-only mode and summarize token usage.

  codex-workspaces use <workspace> [--no-stop] [--no-start] [--force]
  codex-workspaces switch <workspace> [--no-stop] [--no-start] [--force]
  codex-workspaces <workspace>
      Switch the ~/.codex link to the selected workspace directory.
      On macOS this quits Codex App, switches the workspace, then starts Codex App.
      On Linux and Windows app control is skipped and only the workspace link changes.

  codex-workspaces stop [--force]
      Quit Codex App. Currently supported on macOS only.

  codex-workspaces start
      Start Codex App. Currently supported on macOS only.

  codex-workspaces restart [--force]
      Restart Codex App. Currently supported on macOS only.

  codex-workspaces init <workspace> [--migrate-current]
      Initialize a new workspace directory under ~/.codex-workspaces/workspaces/.
      --migrate-current migrates the current real ~/.codex directory into that workspace.

  codex-workspaces migrate [--dry-run] [--from-prefix path] [--from-accounts path]
      Migrate legacy ~/.codex-<workspace> directories and optionally import old ~/.codex-accounts.

  codex-workspaces accounts list
  codex-workspaces accounts current
  codex-workspaces accounts info <account>
  codex-workspaces accounts init <account>
  codex-workspaces accounts save <account>
  codex-workspaces accounts add <account> --login [--timeout seconds] [--keep-temp]
  codex-workspaces accounts login-temp <account>
  codex-workspaces accounts use <account>
  codex-workspaces accounts restore-default [workspace]
  codex-workspaces accounts set-default <workspace> <account> [--activate]
  codex-workspaces accounts rename <old-account> <new-account>
  codex-workspaces accounts delete <account> --force
  codex-workspaces accounts note <account> [note text|--clear]
  codex-workspaces accounts cleanup-login-temp
  codex-workspaces accounts import-workspaces
  codex-workspaces accounts import-legacy <legacy-accounts-dir>
      Manage auth.json account snapshots. accounts add --login creates a temporary login workspace and saves the account after login.

  codex-workspaces rename <old-workspace> <new-workspace>
      Rename a workspace. If it is active, the active link is updated.

  codex-workspaces delete <workspace> --force
      Delete a workspace directory. The active workspace cannot be deleted.

  codex-workspaces note <workspace> [note text|--clear]
      Show, set, or clear a workspace note.

  codex-workspaces install [directory]
      Install a Python launcher into a PATH directory. pipx or pip is preferred.

  codex-workspaces help
      Show this help.

Environment variables:
  CODEX_APP_NAME        App name, default: Codex
  CODEX_QUIT_TIMEOUT    Seconds to wait for app exit, default: 20
  CODEX_WORKSPACES_LINK   Active workspace link, default: ~/.codex
  CODEX_WORKSPACES_ROOT   Managed root directory, default: ~/.codex-workspaces
  CODEX_WORKSPACES_RESTORE_POLICY  Account restore policy: workspace-default, last-active, keep-current
  CODEX_WORKSPACES_LANG   Force output language: zh or en"""
