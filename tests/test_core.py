from __future__ import annotations

import base64
import io
import json
import os
import sqlite3
import stat
import subprocess
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from codex_workspaces.config import Config
from codex_workspaces.auth_inspector import inspect_auth_file
from codex_workspaces.core import WorkspaceManager, strip_workspace_name, validate_workspace_name
from codex_workspaces.errors import CodexWorkspacesError
from codex_workspaces.private_api.errors import PrivateApiAuthError, PrivateApiForbiddenError, PrivateApiNetworkError, PrivateApiRateLimitedError, PrivateApiUnsupportedResponseError
from codex_workspaces.private_api.auth import extract_auth_material
from codex_workspaces.private_api.client import ConfiguredHttpPrivateApiProvider, quota_from_response
from codex_workspaces.private_api.models import AccountRemoteInfo, QuotaInfo
import codex_workspaces.platforms as platforms_module
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


class StubbornMacPlatform(SystemPlatform):
    def __init__(self) -> None:
        super().__init__(env={})
        self.system = "darwin"

    def app_running_status(self, app_name: str):
        return True


def make_config(tmp_path: Path, lang: str = "en", restore_policy: str = "workspace-default") -> Config:
    home = tmp_path / "home"
    home.mkdir(parents=True)
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
        cache_dir=root / "cache",
        lock_file=root / "lock",
        workspace_prefix=str(workspaces) + "/",
        quit_timeout=20,
        lang=lang,
        restore_policy=restore_policy,
    )


def make_manager(tmp_path: Path, platform: FakePlatform | None = None, lang: str = "en", restore_policy: str = "workspace-default", private_api_provider=None):
    stdout = io.StringIO()
    stderr = io.StringIO()
    manager = WorkspaceManager(
        make_config(tmp_path, lang=lang, restore_policy=restore_policy),
        platform or FakePlatform(),
        stdout,
        stderr,
        private_api_provider=private_api_provider,
    )
    return manager, stdout, stderr


def assert_secure_file_mode(path: Path) -> None:
    assert path.is_file()
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def chatgpt_auth_json(
    *,
    access_token: str = "secret-token",
    refresh_token: str = "refresh-secret",
    account_id: str = "chatgpt-account-id",
    auth_mode: str = "chatgpt",
    id_token: str | None = None,
) -> str:
    tokens = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "account_id": account_id,
    }
    if id_token is not None:
        tokens["id_token"] = id_token
    return json.dumps({"auth_mode": auth_mode, "tokens": tokens}) + "\n"


def unsigned_jwt(payload: dict) -> str:
    def encode(data: dict) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode({'alg': 'none'})}.{encode(payload)}."


class MockPrivateApiProvider:
    def __init__(self) -> None:
        self.quota_calls = []
        self.refresh_calls = []
        self.quota_error = None
        self.refresh_error = None
        self.quota = QuotaInfo(status="ok", used_percent=72.0, remaining_percent=28.0, reset_at="2026-07-05T04:00:00+08:00", plan="Plus")

    def get_quota(self, auth):
        self.quota_calls.append(auth)
        if self.quota_error:
            raise self.quota_error
        return self.quota

    def refresh_account(self, auth):
        self.refresh_calls.append(auth)
        if self.refresh_error:
            raise self.refresh_error
        return AccountRemoteInfo(
            email="remote@example.com",
            account_id="remote_acc",
            user_id="remote_user",
            organization_id="remote_org",
            plan="Plus",
            quota=self.quota,
            fetched_at="2026-07-04T20:30:00+08:00",
        )


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


def seed_detailed_state_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE threads (
                title TEXT,
                model TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                created_at TEXT,
                created_at_ms INTEGER
            )
            """
        )
        now = datetime.now(timezone.utc)
        rows = [
            ("Build docs", "gpt-5.5", 1000, 300, now.isoformat(), int(now.timestamp() * 1000)),
            ("Fix tests", "gpt-5.4", 2500, 700, (now - timedelta(days=1)).isoformat(), int((now - timedelta(days=1)).timestamp() * 1000)),
            ("Old task", "gpt-5.5", 4000, 900, (now - timedelta(days=20)).isoformat(), int((now - timedelta(days=20)).timestamp() * 1000)),
        ]
        conn.executemany(
            "INSERT INTO threads (title, model, input_tokens, output_tokens, created_at, created_at_ms) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def build_account_backup(path: Path, *, auth_payload: bytes, auth_hash_value: str) -> None:
    root = path.parent / "backup-root"
    account_dir = root / "codex-workspaces-accounts-backup" / "accounts" / "acct_work"
    account_dir.mkdir(parents=True)
    (root / "codex-workspaces-accounts-backup" / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "codex-workspaces.accounts.backup",
                "created_at": "2026-07-04T20:00:00+08:00",
                "tool_version": "test",
                "include_auth": True,
                "accounts": [{"id": "acct_work", "name": "work", "source": "manual", "auth_hash": auth_hash_value}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (account_dir / "meta.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "acct_work",
                "name": "work",
                "source": "manual",
                "bound_workspace": None,
                "email": None,
                "plan": None,
                "account_id": None,
                "user_id": None,
                "organization_id": None,
                "auth_hash": auth_hash_value,
                "created_at": "2026-07-04T20:00:00+08:00",
                "updated_at": "2026-07-04T20:00:00+08:00",
                "last_used_at": None,
                "notes": "",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (account_dir / "auth.json").write_bytes(auth_payload)
    with tarfile.open(path, "w:gz") as archive:
        archive.add(root / "codex-workspaces-accounts-backup", arcname="codex-workspaces-accounts-backup")


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


class TestAuthInspector:
    def test_inspects_nested_auth_metadata_without_sensitive_keys(self, tmp_path: Path) -> None:
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(
            json.dumps(
                {
                    "profile": {
                        "email": "dev@example.com",
                        "account_id": "acc_123",
                        "user": {"id": "usr_123"},
                        "organization": {"id": "org_123"},
                        "plan": "pro",
                        "access_token": "secret-token",
                    }
                }
            ),
            encoding="utf-8",
        )

        inspection = inspect_auth_file(auth_path)

        assert inspection.email == "dev@example.com"
        assert inspection.account_id == "acc_123"
        assert inspection.user_id in {"usr_123", "id"}
        assert inspection.organization_id == "org_123"
        assert inspection.plan == "pro"
        assert inspection.auth_hash.startswith("sha256:")
        assert "access_token" not in inspection.raw_keys

    def test_inspector_warns_on_invalid_json(self, tmp_path: Path) -> None:
        auth_path = tmp_path / "auth.json"
        auth_path.write_text("{broken", encoding="utf-8")

        inspection = inspect_auth_file(auth_path)

        assert inspection.auth_hash.startswith("sha256:")
        assert inspection.email is None
        assert inspection.warnings


class TestSystemPlatform:
    def test_force_stop_waits_short_grace_period_before_killall(self, monkeypatch) -> None:
        platform = StubbornMacPlatform()
        stdout = io.StringIO()
        calls = []
        sleeps = []

        def fake_run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)

        monkeypatch.setattr(platforms_module.subprocess, "run", fake_run)
        monkeypatch.setattr(platforms_module.time, "sleep", lambda seconds: sleeps.append(seconds))

        platform.stop_app("Codex", timeout=20, force=True, stdout=stdout)

        assert ["osascript", "-e", 'tell application "Codex" to quit'] in calls
        assert ["killall", "Codex"] in calls
        assert sleeps == [1] * (platforms_module.FORCE_QUIT_GRACE_SECONDS + 1)
        assert f"did not exit within {platforms_module.FORCE_QUIT_GRACE_SECONDS}s" in stdout.getvalue()


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
        assert "total tokens: 3,500" in output
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

    def test_stats_summary_json_markdown_and_aggregates(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])
        manager.init_workspace("personal", [])
        seed_detailed_state_db(manager.workspace_dir("work") / "state_5.sqlite")
        seed_detailed_state_db(manager.workspace_dir("personal") / "state_5.sqlite")
        meta = manager.store.ensure_workspace_meta("work", manager.workspace_dir("work"))
        meta.active_account_id = "acct_work"
        manager.store.write_workspace_meta(manager.workspace_dir("work"), meta)

        manager.show_stats(view="models", days=30)
        output = stdout.getvalue()
        assert "Stats Summary" in output
        assert "Top Models:" in output
        assert "gpt-5.5" in output

        stdout.seek(0)
        stdout.truncate(0)
        manager.show_stats(view="daily", days=2, output_format="markdown")
        assert "| Date | Input | Output | Total | Sessions |" in stdout.getvalue()

        stdout.seek(0)
        stdout.truncate(0)
        manager.show_stats(view="accounts", days=30, output_format="json")
        data = json.loads(stdout.getvalue())
        assert data["totals"]["input_tokens"] > 0
        assert data["daily"]
        assert data["models"][0]["model"]
        assert data["workspaces"]
        assert data["accounts"]

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
        assert "auth" in output.lower()
        assert "default+active" in output
        assert "*" in output

        stdout.seek(0)
        stdout.truncate(0)
        manager.accounts_info("acct_work")
        info_output = stdout.getvalue()
        assert "Account: acct_work" in info_output
        assert "auth_exists: yes" in info_output
        assert "default_workspaces: work" in info_output
        assert "auth_hash: sha256:" in info_output

    def test_accounts_save_parses_auth_metadata_without_overwriting_existing_meta(self, tmp_path: Path) -> None:
        manager, _, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])
        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        auth_path = manager.workspace_dir("work") / "auth.json"
        auth_path.write_text(
            json.dumps(
                {
                    "email": "parsed@example.com",
                    "account_id": "acc_123",
                    "user": {"id": "usr_123"},
                    "organization": {"id": "org_123"},
                    "plan": "pro",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        manager.accounts_save("work")
        meta = manager.store.read_account_meta("acct_work")
        assert meta.email == "parsed@example.com"
        assert meta.account_id == "acc_123"
        assert meta.user_id == "usr_123"
        assert meta.organization_id == "org_123"
        assert meta.plan == "pro"

        meta.email = "manual@example.com"
        manager.store.write_account_meta(meta)
        auth_path.write_text('{"email":"new@example.com"}\n', encoding="utf-8")
        manager.accounts_save("work")
        assert manager.store.read_account_meta("acct_work").email == "manual@example.com"

        manager.accounts_refresh_meta(["work", "--overwrite"])
        assert manager.store.read_account_meta("acct_work").email == "new@example.com"

    def test_accounts_export_import_backup_workflow(self, tmp_path: Path) -> None:
        manager, _, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])
        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        (manager.workspace_dir("work") / "auth.json").write_text('{"email":"work@example.com"}\n', encoding="utf-8")
        manager.accounts_save("work")
        backup = tmp_path / "accounts.tar.gz"

        manager.accounts_export(str(backup), ["--include-auth", "--yes", "--account", "work"])

        assert_secure_file_mode(backup)
        with tarfile.open(backup, "r:gz") as archive:
            names = archive.getnames()
        assert "codex-workspaces-accounts-backup/accounts/acct_work/auth.json" in names

        imported, imported_stdout, _ = make_manager(tmp_path / "imported")
        imported.accounts_import_backup(str(backup), ["--dry-run"])
        assert not imported.store.account_dir("acct_work").exists()
        assert "will import:" in imported_stdout.getvalue()

        imported.accounts_import_backup(str(backup), [])
        assert_secure_file_mode(imported.store.account_auth_path("acct_work"))
        assert imported.store.read_account_meta("acct_work").email == "work@example.com"

        imported.accounts_import_backup(str(backup), [])
        assert "will skip existing:" in imported_stdout.getvalue()

        imported.accounts_import_backup(str(backup), ["--rename-conflicts"])
        assert any(account.id.startswith("acct_work_imported_") for account in imported.store.list_accounts())

    def test_accounts_export_without_include_auth_is_meta_only(self, tmp_path: Path) -> None:
        manager, _, _ = make_manager(tmp_path)
        manager.accounts_init("work")
        backup = tmp_path / "meta-only.tar.gz"

        manager.accounts_export(str(backup), ["--all"])

        with tarfile.open(backup, "r:gz") as archive:
            names = archive.getnames()
        assert "codex-workspaces-accounts-backup/accounts/acct_work/meta.json" in names
        assert "codex-workspaces-accounts-backup/accounts/acct_work/auth.json" not in names

    def test_accounts_import_overwrite_backs_up_existing_account(self, tmp_path: Path) -> None:
        source, _, _ = make_manager(tmp_path / "source")
        source.accounts_init("work")
        backup_auth = source.store.account_dir("acct_work") / "auth.json"
        backup_auth.write_text('{"email":"new@example.com"}\n', encoding="utf-8")
        source.store.save_auth_to_account("acct_work", backup_auth)
        backup = tmp_path / "overwrite.tar.gz"
        source.accounts_export(str(backup), ["--include-auth", "--yes", "--all"])

        target, _, _ = make_manager(tmp_path / "target")
        target.accounts_init("work")
        old_auth = target.store.account_dir("acct_work") / "auth.json"
        old_auth.write_text('{"email":"old@example.com"}\n', encoding="utf-8")
        target.store.save_auth_to_account("acct_work", old_auth)

        target.accounts_import_backup(str(backup), ["--overwrite"])

        assert target.store.read_account_meta("acct_work").email == "new@example.com"
        assert any(target.config.backups_dir.glob("*/before-account-import/acct_work/meta.json"))

    def test_accounts_import_rejects_bad_hash_and_unsafe_tar_path(self, tmp_path: Path) -> None:
        manager, _, _ = make_manager(tmp_path)
        manager.accounts_init("work")
        auth = manager.store.account_dir("acct_work") / "auth.json"
        auth.write_text('{"email":"work@example.com"}\n', encoding="utf-8")
        manager.store.save_auth_to_account("acct_work", auth)
        mismatch = tmp_path / "mismatch.tar.gz"
        build_account_backup(mismatch, auth_payload=b'{"changed":true}\n', auth_hash_value="sha256:not-the-real-hash")

        with pytest.raises(CodexWorkspacesError, match="auth_hash mismatch"):
            manager.accounts_import_backup(str(mismatch), ["--dry-run"])

        unsafe = tmp_path / "unsafe.tar.gz"
        with tarfile.open(unsafe, "w:gz") as archive:
            payload = tmp_path / "payload.txt"
            payload.write_text("bad", encoding="utf-8")
            archive.add(payload, arcname="../evil.txt")
        with pytest.raises(CodexWorkspacesError, match="Unsafe backup path"):
            manager.accounts_import_backup(str(unsafe), ["--dry-run"])

    def test_private_api_defaults_disabled_and_config_validation(self, tmp_path: Path) -> None:
        provider = MockPrivateApiProvider()
        manager, stdout, _ = make_manager(tmp_path, private_api_provider=provider)
        manager.init_workspace("work", [])
        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        (manager.workspace_dir("work") / "auth.json").write_text(chatgpt_auth_json(), encoding="utf-8")
        manager.accounts_save("work")
        manager.accounts_set_default("work", "work", activate=True)

        defaults = manager.default_private_api_config()
        assert defaults["base_url"] == "https://chatgpt.com"
        assert defaults["quota_endpoint"] == "/backend-api/wham/usage"
        assert defaults["account_endpoint"] == ""
        assert defaults["enabled"] is False
        assert defaults["quota_enabled"] is False
        legacy_config = manager.read_tool_config()
        legacy_config["experimental_private_api"]["base_url"] = ""
        legacy_config["experimental_private_api"]["quota_endpoint"] = ""
        manager.write_tool_config(legacy_config)
        migrated_settings = manager.private_api_settings()
        assert migrated_settings["base_url"] == "https://chatgpt.com"
        assert migrated_settings["quota_endpoint"] == "/backend-api/wham/usage"

        with pytest.raises(PrivateApiAuthError):
            raise PrivateApiAuthError("authorization bearer secret-token access_token")
        assert manager.show_quota() == 2
        assert "experimental private API features are disabled" in stdout.getvalue()
        assert provider.quota_calls == []

        manager.config_get("experimental_private_api.enabled")
        assert "false" in stdout.getvalue().lower()
        with pytest.raises(CodexWorkspacesError, match="Boolean"):
            manager.config_set("experimental_private_api.enabled", "maybe")

        manager.config_set("experimental_private_api.enabled", "true")
        manager.config_set("experimental_private_api.quota_enabled", "true")
        settings = manager.private_api_settings()
        assert settings["enabled"] is True
        assert settings["quota_enabled"] is True

    def test_quota_current_account_specific_account_cache_and_json_redaction(self, tmp_path: Path) -> None:
        provider = MockPrivateApiProvider()
        manager, stdout, _ = make_manager(tmp_path, private_api_provider=provider)
        manager.init_workspace("work", [])
        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        (manager.workspace_dir("work") / "auth.json").write_text(chatgpt_auth_json(), encoding="utf-8")
        manager.accounts_save("work")
        manager.accounts_set_default("work", "work", activate=True)
        manager.config_set("experimental_private_api.enabled", "true")
        manager.config_set("experimental_private_api.quota_enabled", "true")
        stdout.seek(0)
        stdout.truncate(0)

        manager.show_quota(json_output=True)
        payload = json.loads(stdout.getvalue())
        assert payload["quota"]["used_percent"] == 72.0
        assert "secret-token" not in stdout.getvalue()
        assert len(provider.quota_calls) == 1

        stdout.seek(0)
        stdout.truncate(0)
        manager.accounts_quota("work", json_output=True)
        assert json.loads(stdout.getvalue())["quota"]["cached"] is True
        assert len(provider.quota_calls) == 1

        stdout.seek(0)
        stdout.truncate(0)
        manager.accounts_quota("work", json_output=True, no_cache=True)
        assert len(provider.quota_calls) == 2
        cache_text = manager.quota_cache_path("acct_work").read_text(encoding="utf-8")
        assert "secret-token" not in cache_text

    def test_extract_auth_material_uses_chatgpt_tokens_and_not_id_token(self, tmp_path: Path) -> None:
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(
            chatgpt_auth_json(access_token="access-secret", refresh_token="refresh-secret", account_id="account-secret", id_token="id-secret"),
            encoding="utf-8",
        )

        auth = extract_auth_material("acct_work", auth_path)

        assert auth.access_token == "access-secret"
        assert auth.refresh_token == "refresh-secret"
        assert auth.openai_account_id == "account-secret"
        assert auth.access_token != "id-secret"

    def test_extract_auth_material_rejects_non_chatgpt_mode(self, tmp_path: Path) -> None:
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(chatgpt_auth_json(auth_mode="api-key"), encoding="utf-8")

        with pytest.raises(PrivateApiAuthError, match="unsupported auth_mode"):
            extract_auth_material("acct_work", auth_path)

    def test_wham_provider_sends_openai_account_header(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"quota":{"status":"ok","used_percent":12}}'

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = {key.lower(): value for key, value in request.header_items()}
            captured["timeout"] = timeout
            return FakeResponse()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        provider = ConfiguredHttpPrivateApiProvider(
            base_url="https://chatgpt.com",
            quota_endpoint="/backend-api/wham/usage",
            account_endpoint=None,
            timeout_seconds=7,
            user_agent="codex-workspaces/test",
        )
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(chatgpt_auth_json(access_token="access-secret", account_id="account-secret"), encoding="utf-8")

        quota = provider.get_quota(extract_auth_material("acct_work", auth_path))

        assert quota.status == "ok"
        assert captured["url"] == "https://chatgpt.com/backend-api/wham/usage"
        assert captured["headers"]["authorization"] == "Bearer access-secret"
        assert captured["headers"]["openai-account-id"] == "account-secret"
        assert captured["headers"]["user-agent"] == "codex-workspaces/test"
        assert captured["timeout"] == 7

    def test_wham_provider_rejects_expired_access_token_before_request(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = []
        monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: calls.append(args) or None)
        provider = ConfiguredHttpPrivateApiProvider(
            base_url="https://chatgpt.com",
            quota_endpoint="/backend-api/wham/usage",
            account_endpoint=None,
            timeout_seconds=7,
            user_agent="codex-workspaces/test",
        )
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(
            chatgpt_auth_json(access_token=unsigned_jwt({"exp": 1}), account_id="account-secret"),
            encoding="utf-8",
        )

        with pytest.raises(PrivateApiAuthError, match="access token expired"):
            provider.get_quota(extract_auth_material("acct_work", auth_path))
        assert calls == []

    def test_accounts_list_with_quota_handles_partial_failures_and_plain_list_is_local(self, tmp_path: Path) -> None:
        provider = MockPrivateApiProvider()
        manager, stdout, _ = make_manager(tmp_path, private_api_provider=provider)
        manager.accounts_init("work")
        auth_path = manager.store.account_dir("acct_work") / "auth.json"
        auth_path.write_text(chatgpt_auth_json(), encoding="utf-8")
        manager.store.save_auth_to_account("acct_work", auth_path)
        manager.accounts_init("old")
        manager.config_set("experimental_private_api.enabled", "true")
        manager.config_set("experimental_private_api.quota_enabled", "true")
        stdout.seek(0)
        stdout.truncate(0)

        manager.accounts_list()
        assert provider.quota_calls == []

        provider.quota_error = PrivateApiRateLimitedError("rate limited bearer secret-token")
        manager.accounts_list(all_with_quota=True, verbose=True)
        output = stdout.getvalue()
        assert "rate-limited:rate limited" in output
        assert "no-auth" in output
        assert "secret-token" not in output

    def test_accounts_refresh_updates_remote_meta_and_summary(self, tmp_path: Path) -> None:
        provider = MockPrivateApiProvider()
        manager, stdout, _ = make_manager(tmp_path, private_api_provider=provider)
        manager.init_workspace("work", [])
        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        (manager.workspace_dir("work") / "auth.json").write_text(chatgpt_auth_json(), encoding="utf-8")
        manager.accounts_save("work")
        manager.accounts_set_default("work", "work", activate=True)
        manager.accounts_init("empty")
        manager.config_set("experimental_private_api.enabled", "true")
        manager.config_set("experimental_private_api.refresh_enabled", "true")
        stdout.seek(0)
        stdout.truncate(0)

        manager.accounts_refresh_remote([])
        meta = manager.store.read_account_meta("acct_work")
        assert meta.email == "remote@example.com"
        assert meta.remote["quota_status"] == "ok"
        assert manager.quota_cache_path("acct_work").is_file()

        stdout.seek(0)
        stdout.truncate(0)
        manager.accounts_refresh_remote(["--all", "--json"])
        payload = json.loads(stdout.getvalue())
        assert payload["summary"]["refreshed"] == 1
        assert payload["summary"]["skipped_no_auth"] == 1
        assert "secret-token" not in stdout.getvalue()

    def test_private_api_error_classes_are_redacted(self) -> None:
        errors = [
            PrivateApiAuthError("401 access_token secret"),
            PrivateApiForbiddenError("403 authorization secret"),
            PrivateApiRateLimitedError("429 bearer secret"),
            PrivateApiNetworkError("timeout refresh_token secret"),
            PrivateApiUnsupportedResponseError("bad cookie secret"),
        ]
        for exc in errors:
            text = str(exc)
            assert "access_token" not in text
            assert "authorization" not in text
            assert "refresh_token" not in text
            assert "cookie" not in text
            assert "secret" not in text

    def test_quota_from_response_parses_wham_primary_window(self) -> None:
        quota = quota_from_response(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 42.5,
                        "limit_window_seconds": 10800,
                        "reset_after_seconds": 120,
                        "reset_at": 1767225600,
                    },
                    "secondary_window": {"used_percent": 10},
                    "allowed": True,
                    "limit_reached": False,
                },
                "credits": {"unlimited": True, "balance": 123},
            }
        )

        assert quota.status == "ok"
        assert quota.used_percent == 42.5
        assert quota.remaining_percent == 57.5
        assert quota.window_duration_mins == 180
        assert quota.reset_at == "2026-01-01T00:00:00+00:00"
        assert quota.plan == "unlimited"

        limited = quota_from_response(
            {
                "rate_limit": {
                    "primary_window": {"used_percent": 100, "limit_window_seconds": 3600, "reset_at": "2026-07-05T04:00:00+08:00"},
                    "allowed": False,
                    "limit_reached": True,
                }
            }
        )
        assert limited.status == "limit_reached"
        assert limited.remaining_percent == 0.0
        assert limited.reset_at == "2026-07-05T04:00:00+08:00"

    def test_quota_from_response_keeps_generic_fallback(self) -> None:
        quota = quota_from_response(
            {
                "quota": {
                    "status": "ok",
                    "used_percent": 25,
                    "remaining_percent": 75,
                    "reset_at": "2026-07-05T04:00:00+08:00",
                    "window_duration_mins": 180,
                    "plan": "Plus",
                }
            }
        )

        assert quota.status == "ok"
        assert quota.used_percent == 25.0
        assert quota.remaining_percent == 75.0
        assert quota.window_duration_mins == 180
        assert quota.plan == "Plus"

    def test_restore_policy_last_active_keeps_workspace_active_account(self, tmp_path: Path) -> None:
        manager, _, _ = make_manager(tmp_path, restore_policy="last-active")
        manager.init_workspace("work", [])
        manager.init_workspace("personal", [])

        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        (manager.workspace_dir("work") / "auth.json").write_text('{"account":"work"}\n', encoding="utf-8")
        manager.accounts_save("work")
        manager.accounts_set_default("work", "work", activate=True)

        manager.switch_workspace("personal", ["--no-stop", "--no-start"], ["switch", "personal"])
        (manager.workspace_dir("personal") / "auth.json").write_text('{"account":"personal"}\n', encoding="utf-8")
        manager.accounts_save("personal")
        manager.accounts_set_default("personal", "personal", activate=True)

        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        manager.accounts_use("personal")
        manager.switch_workspace("personal", ["--no-stop", "--no-start"], ["switch", "personal"])
        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])

        meta = manager.store.read_workspace_meta(manager.workspace_dir("work"), "work")
        assert meta.default_account_id == "acct_work"
        assert meta.active_account_id == "acct_personal"
        assert (manager.workspace_dir("work") / "auth.json").read_text(encoding="utf-8") == '{"account":"personal"}\n'

    def test_restore_policy_keep_current_carries_previous_account(self, tmp_path: Path) -> None:
        manager, _, _ = make_manager(tmp_path, restore_policy="keep-current")
        manager.init_workspace("work", [])
        manager.init_workspace("personal", [])

        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        (manager.workspace_dir("work") / "auth.json").write_text('{"account":"work"}\n', encoding="utf-8")
        manager.accounts_save("work")
        manager.accounts_set_default("work", "work", activate=True)

        manager.switch_workspace("personal", ["--no-stop", "--no-start"], ["switch", "personal"])
        (manager.workspace_dir("personal") / "auth.json").write_text('{"account":"personal"}\n', encoding="utf-8")
        manager.accounts_save("personal")
        manager.accounts_set_default("personal", "personal", activate=True)

        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])

        meta = manager.store.read_workspace_meta(manager.workspace_dir("work"), "work")
        assert meta.default_account_id == "acct_work"
        assert meta.active_account_id == "acct_personal"
        assert (manager.workspace_dir("work") / "auth.json").read_text(encoding="utf-8") == '{"account":"personal"}\n'

    def test_workspace_info_shows_metadata(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])
        manager.note_workspace("work", ["main", "workspace"])
        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])

        manager.workspace_info("work")

        output = stdout.getvalue()
        assert "Workspace: work" in output
        assert "active: yes" in output
        assert "note: main workspace" in output
        assert "last_used_at:" in output

    def test_accounts_add_login_temp_saves_account_and_restores_workspace(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        manager, stdout, _ = make_manager(tmp_path, FakePlatform(app_control=True))
        manager.init_workspace("work", [])
        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        (manager.workspace_dir("work") / "auth.json").write_text('{"account":"work"}\n', encoding="utf-8")
        manager.accounts_save("work")
        manager.accounts_set_default("work", "work", activate=True)

        def fake_start_codex() -> None:
            current = manager.current_target()
            if current.kind == "target" and current.path is not None:
                name = manager.current_name(current.path) or ""
                if name.startswith("login-"):
                    (current.path / "auth.json").write_text('{"account":"research"}\n', encoding="utf-8")

        monkeypatch.setattr(manager, "start_codex", fake_start_codex)

        manager.accounts_add("research", ["--login"])

        assert manager.store.account_auth_path("acct_research").read_text(encoding="utf-8") == '{"account":"research"}\n'
        assert manager.current_name(manager.current_target().path) == "work"
        assert not manager.workspace_dir("login-research").exists()
        assert (manager.workspace_dir("work") / "auth.json").read_text(encoding="utf-8") == '{"account":"work"}\n'
        assert "Added account: acct_research" in stdout.getvalue()

    def test_accounts_list_marks_orphans_and_active_only(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])
        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        (manager.workspace_dir("work") / "auth.json").write_text('{"account":"work"}\n', encoding="utf-8")
        manager.accounts_save("work")
        manager.store.create_account("acct_orphan", name="orphan", source="standalone", bound_workspace=None, auth_source=None)
        manual_auth = tmp_path / "manual-auth.json"
        manual_auth.write_text('{"account":"manual"}\n', encoding="utf-8")
        manager.store.create_account("acct_manual", name="manual", source="manual", bound_workspace=None, auth_source=manual_auth)
        manager.accounts_use("manual")

        manager.accounts_list()

        output = stdout.getvalue()
        assert "active-only" in output
        assert "orphan" in output
        assert "acct_orphan" in output

    def test_doctor_reports_account_diagnostics(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])
        (manager.workspace_dir("work") / "auth.json").write_text('{"account":"work"}\n', encoding="utf-8")
        manager.store.create_account("acct_orphan", name="orphan", source="standalone", bound_workspace=None, auth_source=None)

        manager.doctor()

        output = stdout.getvalue()
        assert "workspace work has auth.json but no default account" in output
        assert "account acct_orphan is not referenced by any workspace" in output

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
        assert "Migration report:" in stdout.getvalue()
        assert "migrated workspaces: 2" in stdout.getvalue()

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
        assert "special files skipped: 2" in output
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

    def test_accounts_note_sets_reads_and_clears_meta_notes(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)
        manager.accounts_init("research")

        manager.accounts_note("research", ["lab", "account"])
        manager.accounts_note("research", [])
        manager.accounts_note("research", ["--clear"])
        manager.accounts_note("research", [])

        meta = manager.store.read_account_meta("acct_research")
        output = stdout.getvalue()
        assert meta.notes == ""
        assert "Updated account note: acct_research" in output
        assert "lab account" in output
        assert "Cleared account note: acct_research" in output
        assert "No note set." in output

    def test_accounts_rename_updates_workspace_default_and_active_refs(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])
        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        (manager.workspace_dir("work") / "auth.json").write_text('{"account":"work"}\n', encoding="utf-8")
        manager.accounts_save("work")
        manager.accounts_set_default("work", "work", activate=True)

        manager.accounts_rename("work", "office")

        meta = manager.store.read_workspace_meta(manager.workspace_dir("work"), "work")
        account_meta = manager.store.read_account_meta("acct_office")
        assert meta.default_account_id == "acct_office"
        assert meta.active_account_id == "acct_office"
        assert account_meta.id == "acct_office"
        assert account_meta.name == "office"
        assert not manager.store.account_dir("acct_work").exists()
        assert manager.store.account_auth_path("acct_office").read_text(encoding="utf-8") == '{"account":"work"}\n'
        assert "Renamed account: acct_work -> acct_office" in stdout.getvalue()

    def test_accounts_delete_requires_force_and_refuses_default_account(self, tmp_path: Path) -> None:
        manager, _, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])
        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        (manager.workspace_dir("work") / "auth.json").write_text('{"account":"work"}\n', encoding="utf-8")
        manager.accounts_save("work")
        manager.accounts_set_default("work", "work", activate=True)

        with pytest.raises(CodexWorkspacesError, match="requires --force"):
            manager.accounts_delete("work", [])
        with pytest.raises(CodexWorkspacesError, match="Cannot delete default account"):
            manager.accounts_delete("work", ["--force"])

        assert manager.store.account_dir("acct_work").is_dir()

    def test_accounts_delete_removes_non_default_and_clears_active_refs(self, tmp_path: Path) -> None:
        manager, stdout, _ = make_manager(tmp_path)
        manager.init_workspace("work", [])
        manager.switch_workspace("work", ["--no-stop", "--no-start"], ["switch", "work"])
        (manager.workspace_dir("work") / "auth.json").write_text('{"account":"work"}\n', encoding="utf-8")
        manager.accounts_save("work")
        manager.accounts_set_default("work", "work", activate=True)
        personal_auth = tmp_path / "personal-auth.json"
        personal_auth.write_text('{"account":"personal"}\n', encoding="utf-8")
        manager.store.create_account(
            "acct_personal",
            name="personal",
            source="manual",
            bound_workspace=None,
            auth_source=personal_auth,
        )
        manager.accounts_use("personal")

        manager.accounts_delete("personal", ["--force"])

        meta = manager.store.read_workspace_meta(manager.workspace_dir("work"), "work")
        assert meta.default_account_id == "acct_work"
        assert meta.active_account_id is None
        assert not manager.store.account_dir("acct_personal").exists()
        assert "Deleted account: acct_personal" in stdout.getvalue()
