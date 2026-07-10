from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence, TextIO

from .config import Config
from .errors import CodexWorkspacesError


CODEX_TERMINAL_ENV = (
    "CODEX_THREAD_ID",
    "CODEX_SANDBOX",
    "CODEX_CI",
)

CODEX_DELEGATE_UNSET_ENV = (
    "CODEX_SHELL",
    "CODEX_THREAD_ID",
    "CODEX_SANDBOX",
    "CODEX_SANDBOX_NETWORK_DISABLED",
    "CODEX_CI",
    "CODEX_INTERNAL_ORIGINATOR_OVERRIDE",
    "__CFBundleIdentifier",
)

FORCE_QUIT_GRACE_SECONDS = 5
DEFAULT_APP_NAME_ALIASES = ("ChatGPT", "Codex")


@dataclass(frozen=True)
class AppProcess:
    pid: int
    command: Optional[Sequence[str]] = None
    command_line: Optional[str] = None


class SystemPlatform:
    def __init__(
        self,
        env: Optional[Mapping[str, str]] = None,
        python_executable: Optional[str] = None,
    ) -> None:
        self.env = dict(os.environ if env is None else env)
        self.python_executable = python_executable or sys.executable
        self.system = platform.system().lower()
        self._last_app_process: Optional[AppProcess] = None

    @property
    def is_macos(self) -> bool:
        return self.system == "darwin"

    @property
    def is_windows(self) -> bool:
        return self.system == "windows"

    @property
    def supports_app_control(self) -> bool:
        return self.is_macos

    @property
    def supports_external_terminal_delegation(self) -> bool:
        return self.is_macos

    def apple_language(self) -> Optional[str]:
        if not self.is_macos or shutil.which("defaults") is None:
            return None
        result = subprocess.run(
            ["defaults", "read", "-g", "AppleLanguages"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            if '"' in line:
                parts = line.split('"')
                if len(parts) >= 2 and parts[1]:
                    return parts[1]
        return None

    def is_codex_terminal(self) -> bool:
        if self.env.get("CODEX_SHELL", "").lower() in {"1", "true", "yes"}:
            return True
        if any(self.env.get(name) for name in CODEX_TERMINAL_ENV):
            return True
        origin = self.env.get("CODEX_INTERNAL_ORIGINATOR_OVERRIDE", "")
        if "codex" in origin.lower():
            return True
        bundle_id = self.env.get("__CFBundleIdentifier", "")
        return bundle_id.startswith("com.openai.codex")

    def is_directory_link(self, path: Path) -> bool:
        if path.is_symlink():
            return True
        if not self.is_windows or not path.exists() or not path.is_dir():
            return False
        try:
            absolute = os.path.normcase(os.path.abspath(path))
            resolved = os.path.normcase(os.path.realpath(path))
        except OSError:
            return False
        return absolute != resolved

    def create_directory_link(self, target: Path, link: Path) -> None:
        try:
            link.symlink_to(target, target_is_directory=True)
            return
        except OSError as exc:
            if not self.is_windows:
                raise exc

        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise CodexWorkspacesError(
                f"Could not create directory link {link} -> {target}: {detail}"
            )

    def remove_directory_link(self, link: Path) -> None:
        if link.is_symlink():
            link.unlink()
            return
        if self.is_windows and self.is_directory_link(link):
            link.rmdir()
            return
        raise CodexWorkspacesError(f"Refusing to remove non-link path: {link}")

    def app_running_status(self, app_name: str) -> Optional[bool]:
        if not self.is_macos:
            processes = self.app_processes(app_name)
            return None if processes is None else bool(processes)
        running_name = self.macos_running_app_name(app_name)
        if running_name is None:
            return None
        return bool(running_name)

    def app_name_candidates(self, app_name: str) -> list[str]:
        candidates = [app_name]
        if app_name.lower() in {name.lower() for name in DEFAULT_APP_NAME_ALIASES}:
            for alias in DEFAULT_APP_NAME_ALIASES:
                if alias.lower() != app_name.lower():
                    candidates.append(alias)
        return candidates

    def macos_running_app_name(self, app_name: str) -> Optional[str]:
        if shutil.which("pgrep") is None:
            return None
        saw_error = False
        for candidate in self.app_name_candidates(app_name):
            result = subprocess.run(
                ["pgrep", "-x", candidate],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                return candidate
            if result.returncode not in {0, 1}:
                saw_error = True
        if saw_error:
            return None
        return ""

    def app_processes(self, app_name: str) -> Optional[list[AppProcess]]:
        if self.is_macos:
            return None
        if self.is_windows:
            return self.windows_app_processes(app_name)
        return self.posix_app_processes(app_name)

    def posix_app_processes(self, app_name: str) -> Optional[list[AppProcess]]:
        if shutil.which("ps") is None:
            return None
        result = subprocess.run(
            ["ps", "-eo", "pid=,comm=,args="],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        processes: list[AppProcess] = []
        app_keys = {candidate.lower() for candidate in self.app_name_candidates(app_name)}
        for line in result.stdout.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            if pid == os.getpid():
                continue
            command_name = Path(parts[1]).name
            command_line = parts[2] if len(parts) > 2 else parts[1]
            command_head = command_line.split(None, 1)[0] if command_line else command_name
            candidates = {
                command_name.lower(),
                Path(command_name).stem.lower(),
                Path(command_head).name.lower(),
                Path(command_head).stem.lower(),
            }
            if not (app_keys & candidates):
                continue
            try:
                command = shlex.split(command_line)
            except ValueError:
                command = [command_head]
            processes.append(AppProcess(pid=pid, command=command, command_line=command_line))
        return processes

    def windows_app_processes(self, app_name: str) -> Optional[list[AppProcess]]:
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            return None
        alias_checks = []
        for candidate in self.app_name_candidates(app_name):
            safe_name = candidate.replace("'", "''")
            alias_checks.append(f"$_.Name -ieq '{safe_name}'")
            alias_checks.append(f"[IO.Path]::GetFileNameWithoutExtension($_.Name) -ieq '{safe_name}'")
        script = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { " + " -or ".join(alias_checks) + " } | "
            "Select-Object ProcessId,Name,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            [powershell, "-NoProfile", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        raw = result.stdout.strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        items = data if isinstance(data, list) else [data]
        processes: list[AppProcess] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                pid = int(item.get("ProcessId"))
            except (TypeError, ValueError):
                continue
            command_line = item.get("CommandLine") if isinstance(item.get("CommandLine"), str) else None
            executable = item.get("ExecutablePath") if isinstance(item.get("ExecutablePath"), str) else None
            command = [executable] if executable else None
            processes.append(AppProcess(pid=pid, command=command, command_line=command_line))
        return processes

    def stop_app(
        self,
        app_name: str,
        timeout: int,
        force: bool,
        stdout: TextIO,
    ) -> None:
        if not self.is_macos:
            self.stop_process_app(app_name, timeout, force, stdout)
            return
        running_name = self.macos_running_app_name(app_name)
        if running_name == "":
            print(f"{app_name} is not running.", file=stdout)
            return
        if running_name is None:
            raise CodexWorkspacesError(f"Cannot confirm whether {app_name} is running.")

        print(f"Quitting {running_name} ...", file=stdout)
        subprocess.run(
            ["osascript", "-e", f'tell application "{running_name}" to quit'],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        wait_limit = min(timeout, FORCE_QUIT_GRACE_SECONDS) if force else timeout
        waited = 0
        while self.app_running_status(app_name) and waited < wait_limit:
            time.sleep(1)
            waited += 1

        if self.app_running_status(app_name):
            if not force:
                raise CodexWorkspacesError(
                    f"{app_name} did not exit within {timeout}s; add --force to force quit"
                )
            print(f"{running_name} did not exit within {wait_limit}s; forcing it to quit.", file=stdout)
            for candidate in self.app_name_candidates(app_name):
                subprocess.run(
                    ["killall", candidate],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            time.sleep(1)

        print(f"{running_name} has quit.", file=stdout)

    def start_app(self, app_name: str) -> None:
        if not self.is_macos:
            self.start_process_app(app_name)
            return
        for candidate in self.app_name_candidates(app_name):
            result = subprocess.run(["open", "-a", candidate], check=False)
            if result.returncode == 0:
                return
        raise CodexWorkspacesError(f"Could not start {app_name}.")

    def stop_process_app(self, app_name: str, timeout: int, force: bool, stdout: TextIO) -> None:
        self._last_app_process = None
        processes = self.app_processes(app_name)
        if processes is None:
            print(f"Cannot inspect {app_name} processes on this platform; skipping app stop.", file=stdout)
            return
        if not processes:
            print(f"{app_name} is not running.", file=stdout)
            return

        self._last_app_process = self.first_restartable_process(processes)
        print(f"Quitting {app_name} ({len(processes)} process{'es' if len(processes) != 1 else ''}) ...", file=stdout)
        self.terminate_processes(processes, force=False)

        wait_limit = min(timeout, FORCE_QUIT_GRACE_SECONDS) if force else timeout
        waited = 0
        while self.app_running_status(app_name) and waited < wait_limit:
            time.sleep(1)
            waited += 1

        if self.app_running_status(app_name):
            if not force:
                raise CodexWorkspacesError(
                    f"{app_name} did not exit within {timeout}s; add --force to force quit"
                )
            print(f"{app_name} did not exit within {wait_limit}s; forcing it to quit.", file=stdout)
            remaining = self.app_processes(app_name) or []
            self.terminate_processes(remaining, force=True)
            time.sleep(1)

        print(f"{app_name} has quit.", file=stdout)

    def first_restartable_process(self, processes: Sequence[AppProcess]) -> Optional[AppProcess]:
        for process in processes:
            if process.command_line or process.command:
                return process
        return None

    def terminate_processes(self, processes: Sequence[AppProcess], *, force: bool) -> None:
        if self.is_windows:
            for process in processes:
                args = ["taskkill", "/PID", str(process.pid), "/T"]
                if force:
                    args.append("/F")
                subprocess.run(args, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        sig = signal.SIGKILL if force else signal.SIGTERM
        for process in processes:
            try:
                os.kill(process.pid, sig)
            except OSError:
                pass

    def start_process_app(self, app_name: str) -> None:
        process = self._last_app_process
        if process is None:
            raise CodexWorkspacesError(
                f"Could not start {app_name}: no previously running process command was recorded."
            )
        if self.is_windows:
            command = process.command_line or (subprocess.list2cmdline(list(process.command)) if process.command else None)
            if not command:
                raise CodexWorkspacesError(f"Could not start {app_name}: recorded process has no command line.")
            subprocess.Popen(command, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        if not process.command:
            raise CodexWorkspacesError(f"Could not start {app_name}: recorded process has no command line.")
        subprocess.Popen(
            list(process.command),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def delegate_to_external_terminal(
        self,
        config: Config,
        action: str,
        argv: Sequence[str],
        stdout: TextIO,
    ) -> None:
        if not self.supports_external_terminal_delegation:
            raise CodexWorkspacesError(
                "Cannot delegate to an external terminal on this platform."
            )

        print(
            f"Detected the built-in Codex terminal; delegating {action} to Terminal.app...",
            file=stdout,
        )
        command = self._delegated_command(config, argv)
        fd, raw_name = tempfile.mkstemp(prefix="codex-workspaces.", dir=tempfile.gettempdir())
        os.close(fd)
        launcher = Path(raw_name + ".command")
        Path(raw_name).rename(launcher)
        launcher.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    command,
                    "status=$?",
                    'printf "\\n[codex-workspaces] Done with exit status %s. You can close this window.\\n" "$status"',
                    'rm -f "$0"',
                    'exit "$status"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
        result = subprocess.run(
            ["open", "-a", "Terminal", str(launcher)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            launcher.unlink(missing_ok=True)
            raise CodexWorkspacesError(
                "Could not open Terminal.app. Open the system Terminal manually and run this command again."
            )

    def _delegated_command(self, config: Config, argv: Sequence[str]) -> str:
        pieces = [f"unset {' '.join(CODEX_DELEGATE_UNSET_ENV)};"]
        exports = {
            "CODEX_APP_NAME": config.app_name,
            "CODEX_QUIT_TIMEOUT": str(config.quit_timeout),
            "CODEX_WORKSPACES_LINK": str(config.active_link),
            "CODEX_WORKSPACES_ROOT": str(config.root_dir),
            "CODEX_WORKSPACES_WORKSPACES_DIR": str(config.workspaces_dir),
            "CODEX_WORKSPACES_ACCOUNTS_DIR": str(config.accounts_dir),
            "CODEX_WORKSPACES_LANG": config.lang,
        }
        for key, value in exports.items():
            pieces.append(f"export {key}={shlex.quote(value)};")
        pieces.append(shlex.quote(self.python_executable))
        pieces.append("-m codex_workspaces")
        pieces.extend(shlex.quote(arg) for arg in argv)
        return " ".join(pieces)
