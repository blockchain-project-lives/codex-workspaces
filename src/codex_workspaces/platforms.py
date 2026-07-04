from __future__ import annotations

import os
import platform
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
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


class SystemPlatform:
    def __init__(
        self,
        env: Optional[Mapping[str, str]] = None,
        python_executable: Optional[str] = None,
    ) -> None:
        self.env = dict(os.environ if env is None else env)
        self.python_executable = python_executable or sys.executable
        self.system = platform.system().lower()

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
        if shutil.which("pgrep") is None:
            return None
        result = subprocess.run(
            ["pgrep", "-x", app_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        return None

    def stop_app(
        self,
        app_name: str,
        timeout: int,
        force: bool,
        stdout: TextIO,
    ) -> None:
        if not self.supports_app_control:
            raise CodexWorkspacesError(
                f"App stop is only supported on macOS. Use --no-stop on this platform."
            )

        running = self.app_running_status(app_name)
        if running is False:
            print(f"{app_name} is not running.", file=stdout)
            return
        if running is None:
            raise CodexWorkspacesError(f"Cannot confirm whether {app_name} is running.")

        print(f"Quitting {app_name} ...", file=stdout)
        subprocess.run(
            ["osascript", "-e", f'tell application "{app_name}" to quit'],
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
            print(f"{app_name} did not exit within {wait_limit}s; forcing it to quit.", file=stdout)
            subprocess.run(
                ["killall", app_name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1)

        print(f"{app_name} has quit.", file=stdout)

    def start_app(self, app_name: str) -> None:
        if not self.supports_app_control:
            raise CodexWorkspacesError("App start is only supported on macOS.")
        result = subprocess.run(["open", "-a", app_name], check=False)
        if result.returncode != 0:
            raise CodexWorkspacesError(f"Could not start {app_name}.")

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
