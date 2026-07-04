from __future__ import annotations

import glob
import os
import platform as platform_module
import re
import shlex
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, TextIO

from .config import Config
from .errors import CodexWorkspacesError
from .platforms import SystemPlatform

WORKSPACE_RE = re.compile(r"^[A-Za-z0-9._-]+$")
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
    return Path(config.workspace_prefix + clean_name)


@dataclass(frozen=True)
class CurrentTarget:
    kind: str
    path: Optional[Path] = None


class WorkspaceManager:
    def __init__(
        self,
        config: Config,
        platform_service: Optional[SystemPlatform] = None,
        stdout: Optional[TextIO] = None,
        stderr: Optional[TextIO] = None,
    ) -> None:
        self.config = config
        self.platform = platform_service or SystemPlatform()
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr

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
        dirs = [Path(path) for path in glob.glob(self.config.workspace_prefix + "*")]
        return sorted(path for path in dirs if path.is_dir())

    def same_path(self, left: Path, right: Path) -> bool:
        left_s = os.path.normcase(os.path.abspath(left))
        right_s = os.path.normcase(os.path.abspath(right))
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
                    "未找到工作区目录。可以先执行: codex-workspaces create work",
                    "No workspace directories found. You can create one with: codex-workspaces create work",
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

    def doctor(self) -> None:
        current = self.current_target()
        workspaces = self.workspace_dirs()
        prefix_parent = Path(self.config.workspace_prefix).parent

        self.info(self.bold(self.message("Codex 工作区诊断", "Codex workspaces doctor")))
        self.info(f"python: {platform_module.python_version()} ({sys.executable})")
        self.info(f"platform: {platform_module.system()} {platform_module.release()}")
        self.info(f"app: {self.config.app_name}")
        self.info(f"active link: {self.config.active_link}")
        self.info(f"workspace prefix: {self.config.workspace_prefix}")
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
        directory = self.workspace_dir(clean_name)
        if not directory.is_dir():
            self.fail(
                f"工作区不存在: {directory}。可先执行: codex-workspaces create {clean_name}",
                f"Workspace does not exist: {directory}. You can create it with: codex-workspaces create {clean_name}",
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

    def create_workspace(self, name: str, args: Sequence[str]) -> None:
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
                "缺少工作区名，例如: codex-workspaces create work",
                "Missing workspace name, for example: codex-workspaces create work",
            )

        clean_name = strip_workspace_name(name)
        validate_workspace_name(clean_name)
        directory = self.workspace_dir(clean_name)
        if directory.exists():
            self.fail(f"工作区目录已存在: {directory}", f"Workspace directory already exists: {directory}")

        if migrate_current:
            self.require_external_terminal("migration")
            self.ensure_app_not_running_for_migration()
            active = self.config.active_link
            if self.platform.is_directory_link(active):
                self.fail(
                    f"{active} 已经是软链接，无需迁移。",
                    f"{active} is already a symlink; there is nothing to migrate.",
                )
            if not active.exists():
                self.fail(f"{active} 不存在，无法迁移。", f"{active} does not exist, so it cannot be migrated.")
            if not active.is_dir():
                self.fail(
                    f"{active} 存在但不是目录，无法迁移。",
                    f"{active} exists but is not a directory, so it cannot be migrated.",
                )
            directory.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(active), str(directory))
            self.platform.create_directory_link(directory, active)
            self.info(self.message(f"已迁移当前工作区: {active} -> {directory}", f"Migrated current workspace: {active} -> {directory}"))
            return

        directory.mkdir(parents=True, exist_ok=False)
        self.info(self.message(f"已创建工作区目录: {directory}", f"Created workspace directory: {directory}"))

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
  工作区目录: ~/.codex-work            工作区名 work
            ~/.codex-personal        工作区名 personal

用法:
  codex-workspaces list | ls
      查看所有工作区目录，并标出当前工作区。

  codex-workspaces current
      显示当前 ~/.codex 指向哪个工作区。

  codex-workspaces doctor
      输出路径、平台、App 控制和当前工作区状态诊断。

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

  codex-workspaces create <工作区名> [--migrate-current]
      创建新的工作区目录 ~/.codex-<工作区名>。
      加 --migrate-current 可将已有的真实 ~/.codex 目录迁移为该工作区。

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
  CODEX_WORKSPACES_PREFIX 工作区目录前缀，默认 ~/.codex-
  CODEX_WORKSPACES_LANG   强制提示语言，可设为 zh 或 en"""

    return """codex-workspaces - Codex multi-workspace switcher

Workspace layout:
  Active workspace: ~/.codex                 symlink/directory link
  Workspace dirs:   ~/.codex-work            workspace name: work
                  ~/.codex-personal        workspace name: personal

Usage:
  codex-workspaces list | ls
      List all workspace directories and mark the active workspace.

  codex-workspaces current
      Show where ~/.codex currently points.

  codex-workspaces doctor
      Print path, platform, app-control, and current workspace diagnostics.

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

  codex-workspaces create <workspace> [--migrate-current]
      Create a new workspace directory ~/.codex-<workspace>.
      Add --migrate-current to migrate an existing real ~/.codex directory.

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
  CODEX_WORKSPACES_PREFIX Workspace directory prefix, default: ~/.codex-
  CODEX_WORKSPACES_LANG   Force output language: zh or en"""
