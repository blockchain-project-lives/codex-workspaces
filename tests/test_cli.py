from __future__ import annotations

import io
from pathlib import Path

import pytest

from codex_workspaces.config import Config
from codex_workspaces.cli import run
from codex_workspaces.core import WorkspaceManager
from codex_workspaces.errors import CodexWorkspacesError
from test_core import FakePlatform


def manager_for(tmp_path: Path) -> WorkspaceManager:
    home = tmp_path / "home"
    home.mkdir()
    config = Config(
        app_name="Codex",
        home_dir=home,
        active_link=home / ".codex",
        workspace_prefix=str(home / ".codex-"),
        quit_timeout=20,
        lang="en",
    )
    return WorkspaceManager(config, FakePlatform(), io.StringIO(), io.StringIO())


class TestCliDispatch:
    def test_create_and_workspace_name_alias(self, tmp_path: Path) -> None:
        manager = manager_for(tmp_path)

        assert run(["create", "work"], manager) == 0
        assert run(["work", "--no-stop", "--no-start"], manager) == 0

        assert manager.current_target().kind == "target"

    def test_current_returns_named_workspace(self, tmp_path: Path) -> None:
        manager = manager_for(tmp_path)

        run(["create", "work"], manager)
        run(["switch", "work", "--no-stop", "--no-start"], manager)
        assert run(["current"], manager) == 0

        assert "work ->" in manager.stdout.getvalue()

    def test_unknown_command_raises_expected_error(self, tmp_path: Path) -> None:
        manager = manager_for(tmp_path)

        with pytest.raises(CodexWorkspacesError, match="Unknown command"):
            run(["missing"], manager)

    def test_help_prints_usage(self, tmp_path: Path) -> None:
        manager = manager_for(tmp_path)

        assert run(["help"], manager) == 0

        assert "Codex multi-workspace switcher" in manager.stdout.getvalue()

    def test_doctor_dispatches(self, tmp_path: Path) -> None:
        manager = manager_for(tmp_path)

        assert run(["doctor"], manager) == 0

        assert "Codex workspaces doctor" in manager.stdout.getvalue()

    def test_rename_delete_and_note_dispatch(self, tmp_path: Path) -> None:
        manager = manager_for(tmp_path)

        assert run(["create", "work"], manager) == 0
        assert run(["note", "work", "daily", "driver"], manager) == 0
        assert run(["rename", "work", "main"], manager) == 0
        assert run(["delete", "main", "--force"], manager) == 0

        output = manager.stdout.getvalue()
        assert "Updated note: work" in output
        assert "Renamed workspace: work -> main" in output
        assert "Deleted workspace: main" in output
