from __future__ import annotations

import io
from pathlib import Path

import pytest

from codex_workspaces.config import Config
from codex_workspaces.core import WorkspaceManager, strip_workspace_name, validate_workspace_name
from codex_workspaces.errors import CodexWorkspacesError
from codex_workspaces.platforms import SystemPlatform


class FakePlatform(SystemPlatform):
    def __init__(
        self,
        *,
        app_control: bool = False,
        codex_terminal: bool = False,
        delegate: bool = False,
        app_running: bool = False,
    ) -> None:
        super().__init__(env={})
        self._app_control = app_control
        self._codex_terminal = codex_terminal
        self._delegate = delegate
        self._app_running = app_running
        self.stop_calls = []
        self.start_calls = []
        self.delegate_calls = []

    @property
    def supports_app_control(self) -> bool:
        return self._app_control

    @property
    def supports_external_terminal_delegation(self) -> bool:
        return self._delegate

    def is_codex_terminal(self) -> bool:
        return self._codex_terminal

    def app_running_status(self, app_name: str):
        return self._app_running

    def stop_app(self, app_name: str, timeout: int, force: bool, stdout) -> None:
        self.stop_calls.append((app_name, timeout, force))

    def start_app(self, app_name: str) -> None:
        self.start_calls.append(app_name)

    def delegate_to_external_terminal(self, config, action, argv, stdout) -> None:
        self.delegate_calls.append((action, list(argv)))


def make_config(tmp_path: Path, lang: str = "en") -> Config:
    home = tmp_path / "home"
    home.mkdir()
    return Config(
        app_name="Codex",
        home_dir=home,
        active_link=home / ".codex",
        workspace_prefix=str(home / ".codex-"),
        quit_timeout=20,
        lang=lang,
    )


def make_manager(tmp_path: Path, platform: FakePlatform | None = None, lang: str = "en"):
    stdout = io.StringIO()
    stderr = io.StringIO()
    manager = WorkspaceManager(
        make_config(tmp_path, lang=lang),
        platform or FakePlatform(),
        stdout,
        stderr,
    )
    return manager, stdout, stderr


class TestWorkspaceNames:
    def test_strip_workspace_name_accepts_paths_and_prefixed_names(self) -> None:
        assert strip_workspace_name("/Users/example/.codex-work") == "work"
        assert strip_workspace_name("C:\\Users\\example\\.codex-personal") == "personal"
        assert strip_workspace_name("team.dev") == "team.dev"

    @pytest.mark.parametrize("name", ["work", "personal_1", "team.dev", "a-b"])
    def test_validate_workspace_name_accepts_safe_names(self, name: str) -> None:
        validate_workspace_name(name)

    @pytest.mark.parametrize("name", ["", ".", "..", "bad/name", "bad name", "中文"])
    def test_validate_workspace_name_rejects_unsafe_names(self, name: str) -> None:
        with pytest.raises(CodexWorkspacesError):
            validate_workspace_name(name)


class TestWorkspaceManager:
    def test_create_switch_and_show_current(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)

        manager.create_workspace("work", [])
        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        manager.show_current()

        output = stdout.getvalue()
        assert "Created workspace directory" in output
        assert "Switched to: work" in output
        assert "work ->" in output
        assert manager.current_target().kind == "target"

    def test_list_workspaces_marks_active_workspace(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)

        manager.create_workspace("work", [])
        manager.create_workspace("personal", [])
        manager.switch_workspace("personal", ["--no-stop", "--no-start"], ["switch", "personal"])
        stdout.seek(0)
        stdout.truncate(0)

        manager.list_workspaces()

        output = stdout.getvalue()
        assert "Codex workspaces" in output
        assert "* personal" in output
        assert "work" in output

    def test_switch_refuses_to_replace_real_directory(self, tmp_path: Path) -> None:
        manager, _, _ = make_manager(tmp_path)
        manager.create_workspace("work", [])
        manager.config.active_link.mkdir()

        with pytest.raises(CodexWorkspacesError, match="not a symlink"):
            manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])

    def test_migrate_current_moves_real_directory_and_links_it(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path, FakePlatform(app_running=False))
        manager.config.active_link.mkdir()
        (manager.config.active_link / "config.toml").write_text("token = 'test'\n", encoding="utf-8")

        manager.create_workspace("personal", ["--migrate-current"])

        target = manager.workspace_dir("personal")
        assert (target / "config.toml").read_text(encoding="utf-8") == "token = 'test'\n"
        assert manager.current_target().kind == "target"
        assert "Migrated current workspace" in stdout.getvalue()

    def test_migrate_current_refuses_when_app_is_running(self, tmp_path: Path) -> None:
        manager, _, _ = make_manager(tmp_path, FakePlatform(app_running=True))
        manager.config.active_link.mkdir()

        with pytest.raises(CodexWorkspacesError, match="is running"):
            manager.create_workspace("personal", ["--migrate-current"])

    def test_default_switch_skips_app_control_on_non_macos_platforms(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path, FakePlatform(app_control=False))
        manager.create_workspace("work", [])

        manager.switch_workspace("work", [], ["switch", "work"])

        output = stdout.getvalue()
        assert "App stop is not supported on this platform" in output
        assert "App start is not supported on this platform" in output
        assert manager.current_target().kind == "target"

    def test_default_switch_uses_app_control_when_supported(self, tmp_path: Path) -> None:
        platform = FakePlatform(app_control=True)
        manager, _, _ = make_manager(tmp_path, platform)
        manager.create_workspace("work", [])

        manager.switch_workspace("work", [], ["switch", "work"])

        assert platform.stop_calls == [("Codex", 20, False)]
        assert platform.start_calls == ["Codex"]

    def test_switch_from_codex_terminal_without_delegation_is_blocked(self, tmp_path: Path) -> None:
        manager, _, _ = make_manager(tmp_path, FakePlatform(codex_terminal=True, delegate=False))
        manager.create_workspace("work", [])

        with pytest.raises(CodexWorkspacesError, match="built-in Codex terminal"):
            manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])

    def test_switch_from_codex_terminal_delegates_when_available(self, tmp_path: Path) -> None:
        platform = FakePlatform(codex_terminal=True, delegate=True)
        manager, _, _ = make_manager(tmp_path, platform)
        manager.create_workspace("work", [])

        manager.switch_workspace("work", ["--no-stop"], ["switch", "work", "--no-stop"])

        assert platform.delegate_calls == [("switch workspaces", ["switch", "work", "--no-stop"])]

    def test_doctor_reports_environment_and_current_state(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)
        manager.create_workspace("work", [])
        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        stdout.seek(0)
        stdout.truncate(0)

        manager.doctor()

        output = stdout.getvalue()
        assert "Codex workspaces doctor" in output
        assert "python:" in output
        assert "platform:" in output
        assert "workspaces found: 1" in output
        assert "current state: work ->" in output
