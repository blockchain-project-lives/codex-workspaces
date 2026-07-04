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
    root = home / ".codex-workspaces"
    workspaces = root / "workspaces"
    accounts = root / "accounts"
    config = Config(
        app_name="Codex",
        home_dir=home,
        root_dir=root,
        active_link=home / ".codex",
        workspaces_dir=workspaces,
        accounts_dir=accounts,
        backups_dir=root / "backups",
        lock_file=root / "lock",
        workspace_prefix=str(workspaces) + "/",
        quit_timeout=20,
        lang="en",
    )
    return WorkspaceManager(config, FakePlatform(), io.StringIO(), io.StringIO())


class TestCliDispatch:
    def test_init_and_workspace_name_alias(self, tmp_path: Path) -> None:
        manager = manager_for(tmp_path)

        assert run(["init", "work"], manager) == 0
        assert run(["work", "--no-stop", "--no-start"], manager) == 0

        assert manager.current_target().kind == "target"

    def test_current_returns_named_workspace(self, tmp_path: Path) -> None:
        manager = manager_for(tmp_path)

        run(["init", "work"], manager)
        run(["switch", "work", "--no-stop", "--no-start"], manager)
        assert run(["current"], manager) == 0
        assert run(["info", "work"], manager) == 0

        output = manager.stdout.getvalue()
        assert "work ->" in output
        assert "Workspace: work" in output

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

    def test_stats_dispatches_with_days(self, tmp_path: Path) -> None:
        from test_core import seed_state_db

        manager = manager_for(tmp_path)
        run(["init", "work"], manager)
        seed_state_db(manager.workspace_dir("work") / "state_5.sqlite")

        assert run(["stats", "work", "--days", "3"], manager) == 0

        output = manager.stdout.getvalue()
        assert "Codex workspace stats: work" in output
        assert "daily tokens last 3 days:" in output

    def test_rename_delete_and_note_dispatch(self, tmp_path: Path) -> None:
        manager = manager_for(tmp_path)

        assert run(["init", "work"], manager) == 0
        assert run(["note", "work", "daily", "driver"], manager) == 0
        assert run(["rename", "work", "main"], manager) == 0
        assert run(["delete", "main", "--force"], manager) == 0

        output = manager.stdout.getvalue()
        assert "Updated note: work" in output
        assert "Renamed workspace: work -> main" in output
        assert "Deleted workspace: main" in output

    def test_accounts_dispatches_phase_one_to_three(self, tmp_path: Path) -> None:
        manager = manager_for(tmp_path)

        assert run(["init", "work"], manager) == 0
        assert run(["work", "--no-stop", "--no-start"], manager) == 0
        (manager.workspace_dir("work") / "auth.json").write_text('{"account":"work"}\n', encoding="utf-8")
        assert run(["accounts", "save", "work"], manager) == 0
        assert run(["accounts", "set-default", "work", "work", "--activate"], manager) == 0
        assert run(["accounts", "current"], manager) == 0
        assert run(["accounts", "list"], manager) == 0
        assert run(["accounts", "info", "work"], manager) == 0
        assert run(["accounts", "restore-default"], manager) == 0

        output = manager.stdout.getvalue()
        assert "acct_work" in output
        assert "active=acct_work default=acct_work" in output
        assert "auth_exists: yes" in output

    def test_account_lifecycle_dispatches(self, tmp_path: Path) -> None:
        manager = manager_for(tmp_path)

        assert run(["accounts", "init", "research"], manager) == 0
        assert run(["accounts", "note", "research", "lab"], manager) == 0
        assert run(["accounts", "rename", "research", "lab"], manager) == 0
        assert run(["accounts", "delete", "lab", "--force"], manager) == 0

        output = manager.stdout.getvalue()
        assert "Initialized account: acct_research" in output
        assert "Updated account note: acct_research" in output
        assert "Renamed account: acct_research -> acct_lab" in output
        assert "Deleted account: acct_lab" in output

    def test_migrate_and_import_legacy_dispatch(self, tmp_path: Path) -> None:
        manager = manager_for(tmp_path)
        legacy = manager.config.home_dir / ".codex-work"
        legacy.mkdir()
        (legacy / "auth.json").write_text('{"account":"work"}\n', encoding="utf-8")
        legacy_accounts = manager.config.home_dir / ".codex-accounts"
        research = legacy_accounts / "research"
        research.mkdir(parents=True)
        (research / "auth.json").write_text('{"account":"research"}\n', encoding="utf-8")

        assert run(["migrate", "--dry-run"], manager) == 0
        assert run(["migrate", "--from-prefix", str(manager.config.home_dir / ".codex-")], manager) == 0
        assert run(["accounts", "import-legacy", str(legacy_accounts)], manager) == 0

        output = manager.stdout.getvalue()
        assert "Migration plan" in output
        assert "migrated workspaces: 1" in output
        assert "Imported legacy accounts: 1" in output
