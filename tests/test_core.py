from __future__ import annotations

import io
import os
import sqlite3
from datetime import datetime, timedelta, timezone
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
    root = home / ".codex-workspaces"
    workspaces = root / "workspaces"
    accounts = root / "accounts"
    return Config(
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


def seed_state_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE threads (
                title TEXT,
                model TEXT,
                tokens_used INTEGER,
                created_at TEXT,
                created_at_ms INTEGER
            )
            """
        )
        now = datetime.now(timezone.utc)
        rows = [
            ("Build docs", "gpt-5.5", 1000, now.isoformat(), int(now.timestamp() * 1000)),
            ("Fix tests", "gpt-5.4", 2500, (now - timedelta(days=2)).isoformat(), int((now - timedelta(days=2)).timestamp() * 1000)),
            ("Old task", "gpt-5.5", 4000, (now - timedelta(days=20)).isoformat(), int((now - timedelta(days=20)).timestamp() * 1000)),
            ("No usage", "gpt-5.5", 0, now.isoformat(), int(now.timestamp() * 1000)),
        ]
        conn.executemany(
            "INSERT INTO threads (title, model, tokens_used, created_at, created_at_ms) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


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
    def test_init_switch_and_show_current(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)

        manager.init_workspace("work", [])
        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        manager.show_current()

        output = stdout.getvalue()
        assert "Initialized workspace directory" in output
        assert "Switched to: work" in output
        assert "work ->" in output
        assert manager.workspace_dir("work") == manager.config.workspaces_dir / "work"
        assert (manager.config.root_dir / "config.json").is_file()
        assert (manager.workspace_dir("work") / ".codex-workspace.json").is_file()
        assert manager.current_target().kind == "target"

    def test_list_workspaces_marks_active_workspace(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)

        manager.init_workspace("work", [])
        (manager.workspace_dir("work") / "config.toml").write_text("hello", encoding="utf-8")
        manager.init_workspace("personal", [])
        manager.switch_workspace("personal", ["--no-stop", "--no-start"], ["switch", "personal"])
        stdout.seek(0)
        stdout.truncate(0)

        manager.list_workspaces()

        output = stdout.getvalue()
        assert "Codex workspaces" in output
        assert "modified" in output
        assert "note" in output
        assert "5 B" in output
        assert "* personal" in output
        assert "work" in output

    def test_directory_size_ignores_unreadable_entries(self, tmp_path: Path) -> None:
        manager, _, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])
        nested = manager.workspace_dir("work") / "nested"
        nested.mkdir()
        (nested / "data.txt").write_text("hello", encoding="utf-8")

        assert manager.directory_size(manager.workspace_dir("work")) >= 5
        assert manager.format_size(1536) == "1.5 KB"

    def test_switch_refuses_to_replace_real_directory(self, tmp_path: Path) -> None:
        manager, _, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])
        manager.config.active_link.mkdir()

        with pytest.raises(CodexWorkspacesError, match="not a symlink"):
            manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])

    def test_init_migrate_current_moves_real_codex_into_managed_workspace(self, tmp_path: Path) -> None:
        manager, _, _ = make_manager(tmp_path)
        manager.config.active_link.mkdir()
        (manager.config.active_link / "auth.json").write_text('{"account":"current"}\n', encoding="utf-8")
        (manager.config.active_link / "config.toml").write_text("model = 'gpt'\n", encoding="utf-8")

        manager.init_workspace("personal", ["--migrate-current"])

        directory = manager.workspace_dir("personal")
        meta = manager.store.read_workspace_meta(directory, "personal")
        assert manager.current_target().kind == "target"
        assert manager.same_path(manager.current_target().path, directory)
        assert (directory / "auth.json").read_text(encoding="utf-8") == '{"account":"current"}\n'
        assert meta.default_account_id == "acct_personal"
        assert meta.active_account_id == "acct_personal"
        assert manager.store.account_auth_path("acct_personal").read_text(encoding="utf-8") == '{"account":"current"}\n'
        assert any(manager.config.backups_dir.glob("*/before-migrate/codex/auth.json"))

    def test_default_switch_skips_app_control_on_non_macos_platforms(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path, FakePlatform(app_control=False))
        manager.init_workspace("work", [])

        manager.switch_workspace("work", [], ["switch", "work"])

        output = stdout.getvalue()
        assert "App stop is not supported on this platform" in output
        assert "App start is not supported on this platform" in output
        assert manager.current_target().kind == "target"

    def test_default_switch_uses_app_control_when_supported(self, tmp_path: Path) -> None:
        platform = FakePlatform(app_control=True)
        manager, _, _ = make_manager(tmp_path, platform)
        manager.init_workspace("work", [])

        manager.switch_workspace("work", [], ["switch", "work"])

        assert platform.stop_calls == [("Codex", 20, False)]
        assert platform.start_calls == ["Codex"]

    def test_switch_from_codex_terminal_without_delegation_is_blocked(self, tmp_path: Path) -> None:
        manager, _, _ = make_manager(tmp_path, FakePlatform(codex_terminal=True, delegate=False))
        manager.init_workspace("work", [])

        with pytest.raises(CodexWorkspacesError, match="built-in Codex terminal"):
            manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])

    def test_switch_from_codex_terminal_delegates_when_available(self, tmp_path: Path) -> None:
        platform = FakePlatform(codex_terminal=True, delegate=True)
        manager, _, _ = make_manager(tmp_path, platform)
        manager.init_workspace("work", [])

        manager.switch_workspace("work", ["--no-stop"], ["switch", "work", "--no-stop"])

        assert platform.delegate_calls == [("switch workspaces", ["switch", "work", "--no-stop"])]

    def test_doctor_reports_environment_and_current_state(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])
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

    def test_stats_reads_current_workspace_state_database(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])
        seed_state_db(manager.workspace_dir("work") / "state_5.sqlite")
        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        stdout.seek(0)
        stdout.truncate(0)

        manager.show_stats(days=7)

        output = stdout.getvalue()
        assert "Codex workspace stats: work" in output
        assert "total tokens: 7,500" in output
        assert "last 7 days: 3,500 (2 sessions)" in output
        assert "by model:" in output
        assert "daily tokens last 7 days:" in output
        assert "recent sessions:" in output

    def test_stats_reads_nested_sqlite_directory_for_named_workspace(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])
        sqlite_dir = manager.workspace_dir("work") / "sqlite"
        sqlite_dir.mkdir()
        seed_state_db(sqlite_dir / "state_6.sqlite")

        manager.show_stats("work", days=3)

        output = stdout.getvalue()
        assert "state_6.sqlite" in output
        assert "daily tokens last 3 days:" in output

    def test_stats_reports_missing_database(self, tmp_path: Path) -> None:
        manager, _, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])

        with pytest.raises(CodexWorkspacesError, match="Could not read stats"):
            manager.show_stats("work")

    def test_rename_workspace_updates_active_link(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])
        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])

        manager.rename_workspace("work", "main")
        manager.show_current()

        output = stdout.getvalue()
        assert "Renamed workspace: work -> main" in output
        assert "main ->" in output
        assert manager.workspace_dir("main").is_dir()
        assert not manager.workspace_dir("work").exists()

    def test_delete_workspace_requires_force_and_refuses_active_workspace(self, tmp_path: Path) -> None:
        manager, _, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])

        with pytest.raises(CodexWorkspacesError, match="requires --force"):
            manager.delete_workspace("work", [])

        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        with pytest.raises(CodexWorkspacesError, match="active workspace"):
            manager.delete_workspace("work", ["--force"])

    def test_delete_workspace_removes_inactive_workspace(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])
        manager.init_workspace("old", [])

        manager.delete_workspace("old", ["--force"])

        assert "Deleted workspace: old" in stdout.getvalue()
        assert not manager.workspace_dir("old").exists()

    def test_note_workspace_sets_reads_clears_and_lists_note(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])

        manager.note_workspace("work", ["main", "profile"])
        manager.note_workspace("work", [])
        manager.list_workspaces()
        manager.note_workspace("work", ["--clear"])
        manager.note_workspace("work", [])

        output = stdout.getvalue()
        assert "Updated note: work" in output
        assert "main profile" in output
        assert "Cleared note: work" in output
        assert "No note set." in output

    def test_accounts_save_use_restore_default_and_workspace_enter_reset(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])
        manager.init_workspace("personal", [])

        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        (manager.workspace_dir("work") / "auth.json").write_text('{"account":"work-v1"}\n', encoding="utf-8")
        manager.accounts_save("acct_work")
        manager.accounts_set_default("work", "acct_work", activate=True)

        manager.switch_workspace("personal", ["--no-stop", "--no-start"], ["switch", "personal"])
        (manager.workspace_dir("personal") / "auth.json").write_text('{"account":"personal"}\n', encoding="utf-8")
        manager.accounts_save("acct_personal")
        manager.accounts_set_default("personal", "acct_personal", activate=True)

        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        (manager.workspace_dir("work") / "auth.json").write_text('{"account":"work-v2"}\n', encoding="utf-8")
        manager.accounts_use("acct_personal")

        work_meta = manager.store.read_workspace_meta(manager.workspace_dir("work"), "work")
        assert work_meta.default_account_id == "acct_work"
        assert work_meta.active_account_id == "acct_personal"
        assert (manager.workspace_dir("work") / "auth.json").read_text(encoding="utf-8") == '{"account":"personal"}\n'
        assert manager.store.account_auth_path("acct_work").read_text(encoding="utf-8") == '{"account":"work-v2"}\n'

        manager.switch_workspace("personal", ["--no-stop", "--no-start"], ["switch", "personal"])
        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])

        work_meta = manager.store.read_workspace_meta(manager.workspace_dir("work"), "work")
        assert work_meta.active_account_id == "acct_work"
        assert work_meta.default_account_id == "acct_work"
        assert (manager.workspace_dir("work") / "auth.json").read_text(encoding="utf-8") == '{"account":"work-v2"}\n'

        manager.accounts_use("acct_personal")
        manager.accounts_restore_default()
        work_meta = manager.store.read_workspace_meta(manager.workspace_dir("work"), "work")
        assert work_meta.active_account_id == "acct_work"
        assert (manager.workspace_dir("work") / "auth.json").read_text(encoding="utf-8") == '{"account":"work-v2"}\n'

        stdout.seek(0)
        stdout.truncate(0)
        manager.accounts_list()
        output = stdout.getvalue()
        assert "acct_work" in output
        assert "acct_personal" in output
        assert "workspace-default" in output
        assert "work" in output
        assert "*" in output

    def test_accounts_restore_default_requires_default_account(self, tmp_path: Path) -> None:
        manager, _, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])
        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])

        with pytest.raises(CodexWorkspacesError, match="no default account"):
            manager.accounts_restore_default()

    def test_account_name_validation_rejects_empty_prefixed_id(self, tmp_path: Path) -> None:
        manager, _, _ = make_manager(tmp_path)

        with pytest.raises(CodexWorkspacesError):
            manager.accounts_init("acct_")

    def test_lock_blocks_account_switch(self, tmp_path: Path) -> None:
        manager, _, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])
        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        (manager.workspace_dir("work") / "auth.json").write_text('{"account":"work"}\n', encoding="utf-8")
        manager.accounts_save("acct_work")
        manager.config.lock_file.write_text("busy\n", encoding="utf-8")

        with pytest.raises(CodexWorkspacesError, match="lock acquisition failed"):
            manager.accounts_use("acct_work")

        manager.config.lock_file.unlink()

    def test_migrate_dry_run_does_not_modify_files(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)
        legacy = manager.config.home_dir / ".codex-work"
        legacy.mkdir()
        (legacy / "auth.json").write_text('{"account":"work"}\n', encoding="utf-8")

        manager.migrate(dry_run=True)

        output = stdout.getvalue()
        assert "Migration plan" in output
        assert str(legacy) in output
        assert "acct_work" in output
        assert not manager.workspace_dir("work").exists()
        assert not manager.config.root_dir.exists()

    def test_migrate_legacy_workspaces_creates_accounts_and_backup(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)
        legacy_work = manager.config.home_dir / ".codex-work"
        legacy_work.mkdir()
        (legacy_work / "auth.json").write_text('{"account":"work"}\n', encoding="utf-8")
        (legacy_work / "state_1.sqlite").write_text("db", encoding="utf-8")
        legacy_personal = manager.config.home_dir / ".codex-personal"
        legacy_personal.mkdir()
        manager.platform.create_directory_link(legacy_work, manager.config.active_link)

        manager.migrate()

        work_dir = manager.workspace_dir("work")
        personal_dir = manager.workspace_dir("personal")
        work_meta = manager.store.read_workspace_meta(work_dir, "work")
        assert work_dir.is_dir()
        assert personal_dir.is_dir()
        assert legacy_work.is_dir()
        assert legacy_personal.is_dir()
        assert manager.current_target().kind == "target"
        assert manager.same_path(manager.current_target().path, work_dir)
        assert work_meta.default_account_id == "acct_work"
        assert work_meta.active_account_id == "acct_work"
        assert manager.store.read_account_meta("acct_work").source == "workspace-default"
        assert manager.store.account_auth_path("acct_work").read_text(encoding="utf-8") == '{"account":"work"}\n'
        assert any(manager.config.backups_dir.glob("*/before-migrate/legacy-workspaces/.codex-work/auth.json"))
        assert "Migrated workspaces: 2" in stdout.getvalue()

    def test_migrate_skips_unsupported_special_files(self, tmp_path: Path) -> None:
        if not hasattr(os, "mkfifo"):
            pytest.skip("mkfifo is not available on this platform")
        manager, stdout, _ = make_manager(tmp_path)
        legacy_work = manager.config.home_dir / ".codex-work"
        legacy_work.mkdir()
        (legacy_work / "auth.json").write_text('{"account":"work"}\n', encoding="utf-8")
        os.mkfifo(legacy_work / "fsmonitor--daemon.ipc")

        manager.migrate()

        output = stdout.getvalue()
        assert "Skipping unsupported special file" in output
        assert manager.workspace_dir("work").is_dir()
        assert not (manager.workspace_dir("work") / "fsmonitor--daemon.ipc").exists()
        assert manager.store.account_auth_path("acct_work").is_file()

    def test_migrate_renames_conflicting_legacy_account_ids(self, tmp_path: Path) -> None:
        manager, _, _ = make_manager(tmp_path)
        legacy_work = manager.config.home_dir / ".codex-work"
        legacy_work.mkdir()
        (legacy_work / "auth.json").write_text('{"account":"workspace"}\n', encoding="utf-8")
        legacy_account = manager.config.home_dir / ".codex-accounts" / "work"
        legacy_account.mkdir(parents=True)
        (legacy_account / "auth.json").write_text('{"account":"legacy"}\n', encoding="utf-8")

        manager.migrate()

        accounts = manager.store.list_accounts()
        account_ids = {account.id for account in accounts}
        imported = [account for account in accounts if account.source == "imported"]
        assert "acct_work" in account_ids
        assert len(imported) == 1
        assert imported[0].id.startswith("acct_work_")
        assert manager.store.account_auth_path(imported[0].id).read_text(encoding="utf-8") == '{"account":"legacy"}\n'

    def test_accounts_import_legacy_imports_old_codex_accounts(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)
        legacy_accounts = manager.config.home_dir / ".codex-accounts"
        account = legacy_accounts / "research"
        account.mkdir(parents=True)
        (account / "auth.json").write_text('{"account":"research"}\n', encoding="utf-8")

        manager.accounts_import_legacy(str(legacy_accounts))

        meta = manager.store.read_account_meta("acct_research")
        assert meta.source == "imported"
        assert meta.bound_workspace is None
        assert manager.store.account_auth_path("acct_research").read_text(encoding="utf-8") == '{"account":"research"}\n'
        assert "Imported legacy accounts: 1" in stdout.getvalue()

    def test_accounts_import_workspaces_creates_missing_default_accounts(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])
        (manager.workspace_dir("work") / "auth.json").write_text('{"account":"work"}\n', encoding="utf-8")

        manager.accounts_import_workspaces()

        meta = manager.store.read_workspace_meta(manager.workspace_dir("work"), "work")
        assert meta.default_account_id == "acct_work"
        assert meta.active_account_id == "acct_work"
        assert manager.store.account_auth_path("acct_work").read_text(encoding="utf-8") == '{"account":"work"}\n'
        assert "Imported workspace default accounts: 1" in stdout.getvalue()
