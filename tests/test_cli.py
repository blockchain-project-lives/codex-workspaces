from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from codex_workspaces.config import Config
from codex_workspaces.cli import run
from codex_workspaces.core import WorkspaceManager
from codex_workspaces.errors import CodexWorkspacesError
from codex_workspaces.private_api.errors import PrivateApiNetworkError, PrivateApiUnsupportedResponseError
from test_core import FakePlatform, MockPrivateApiProvider, chatgpt_auth_json


def manager_for(tmp_path: Path, private_api_provider=None) -> WorkspaceManager:
    home = tmp_path / "home"
    home.mkdir(parents=True)
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
        cache_dir=root / "cache",
        lock_file=root / "lock",
        workspace_prefix=str(workspaces) + "/",
        quit_timeout=20,
        lang="en",
    )
    return WorkspaceManager(config, FakePlatform(), io.StringIO(), io.StringIO(), private_api_provider=private_api_provider)


def prepare_quota_account(manager: WorkspaceManager) -> None:
    assert run(["init", "work"], manager) == 0
    assert run(["work", "--no-stop", "--no-start"], manager) == 0
    (manager.workspace_dir("work") / "auth.json").write_text(
        chatgpt_auth_json(),
        encoding="utf-8",
    )
    assert run(["accounts", "save", "work"], manager) == 0
    assert run(["accounts", "set-default", "work", "work", "--activate"], manager) == 0


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

    def test_stats_dispatches_new_views_and_json_format(self, tmp_path: Path) -> None:
        from test_core import seed_detailed_state_db

        manager = manager_for(tmp_path)
        run(["init", "work"], manager)
        seed_detailed_state_db(manager.workspace_dir("work") / "state_5.sqlite")
        manager.stdout.seek(0)
        manager.stdout.truncate(0)

        assert run(["stats", "models", "--workspace", "work", "--days", "30", "--format", "json"], manager) == 0

        data = json.loads(manager.stdout.getvalue())
        assert data["totals"]["total_tokens"] > 0
        assert data["models"]

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

    def test_accounts_current_id_json_and_info_composition(self, tmp_path: Path) -> None:
        manager = manager_for(tmp_path)

        assert run(["init", "work"], manager) == 0
        assert run(["work", "--no-stop", "--no-start"], manager) == 0
        (manager.workspace_dir("work") / "auth.json").write_text(chatgpt_auth_json(), encoding="utf-8")
        assert run(["accounts", "save", "work"], manager) == 0
        assert run(["accounts", "set-default", "work", "work", "--activate"], manager) == 0
        manager.stdout.seek(0)
        manager.stdout.truncate(0)

        assert run(["accounts", "current", "--id"], manager) == 0
        account_id = manager.stdout.getvalue().strip()
        assert account_id == "acct_work"

        manager.stdout.seek(0)
        manager.stdout.truncate(0)
        assert run(["accounts", "current", "--json"], manager) == 0
        payload = json.loads(manager.stdout.getvalue())
        assert payload == {
            "active_account_id": "acct_work",
            "default_account_id": "acct_work",
            "workspace": "work",
        }

        manager.stdout.seek(0)
        manager.stdout.truncate(0)
        assert run(["accounts", "info", account_id], manager) == 0
        assert "Account: acct_work" in manager.stdout.getvalue()

    def test_accounts_refresh_export_and_import_dispatch(self, tmp_path: Path) -> None:
        manager = manager_for(tmp_path)

        assert run(["accounts", "init", "work"], manager) == 0
        auth_path = manager.store.account_dir("acct_work") / "auth.json"
        auth_path.write_text('{"email":"work@example.com"}\n', encoding="utf-8")
        manager.store.save_auth_to_account("acct_work", auth_path)
        backup = tmp_path / "accounts.tar.gz"
        assert run(["accounts", "refresh-meta", "work"], manager) == 0
        assert run(["accounts", "export", str(backup), "--include-auth", "--yes", "--all"], manager) == 0

        imported = manager_for(tmp_path / "imported")
        assert run(["accounts", "import", str(backup), "--dry-run"], imported) == 0

        output = manager.stdout.getvalue() + imported.stdout.getvalue()
        assert "Refreshed account metadata" in output
        assert "Exported account backup" in output
        assert "Import Plan:" in output

    def test_config_quota_and_accounts_quota_dispatch(self, tmp_path: Path) -> None:
        provider = MockPrivateApiProvider()
        manager = manager_for(tmp_path, private_api_provider=provider)

        prepare_quota_account(manager)
        assert run(["config", "set", "experimental_private_api.enabled", "true"], manager) == 0
        assert run(["config", "set", "experimental_private_api.quota_enabled", "true"], manager) == 0
        assert run(["quota", "--json"], manager) == 0
        assert run(["accounts", "quota", "work", "--json"], manager) == 0
        assert run(["accounts", "list", "-a", "--json"], manager) == 0

        output = manager.stdout.getvalue()
        assert "secret-token" not in output
        assert provider.quota_calls

    def test_quota_disabled_returns_friendly_error_without_traceback(self, tmp_path: Path) -> None:
        manager = manager_for(tmp_path)
        prepare_quota_account(manager)
        manager.stdout.seek(0)
        manager.stdout.truncate(0)

        assert run(["quota"], manager) == 2

        output = manager.stdout.getvalue()
        assert "experimental private API features are disabled" in output
        assert "Enable explicitly" in output
        assert "Traceback" not in output
        assert "secret-token" not in output
        assert "authorization" not in output.lower()
        assert "cookie" not in output.lower()

    def test_quota_endpoint_missing_returns_text_and_json_errors(self, tmp_path: Path) -> None:
        manager = manager_for(tmp_path)
        prepare_quota_account(manager)
        assert run(["config", "set", "experimental_private_api.enabled", "true"], manager) == 0
        assert run(["config", "set", "experimental_private_api.quota_enabled", "true"], manager) == 0
        assert run(["config", "set", "experimental_private_api.provider", "custom"], manager) == 0
        assert run(["config", "set", "experimental_private_api.quota_endpoint", ""], manager) == 0

        manager.stdout.seek(0)
        manager.stdout.truncate(0)
        assert run(["quota"], manager) == 2
        output = manager.stdout.getvalue()
        assert "ERROR: realtime quota is not configured." in output
        assert "quota endpoint is not configured" in output
        assert "codex-workspaces config set experimental_private_api.quota_enabled false" in output
        assert "Traceback" not in output
        assert "secret-token" not in output

        manager.stdout.seek(0)
        manager.stdout.truncate(0)
        assert run(["quota", "--json"], manager) == 2
        payload = json.loads(manager.stdout.getvalue())
        assert payload["status"] == "error"
        assert payload["account"] == "acct_work"
        assert payload["workspace"] == "work"
        assert payload["error"]["type"] == "unsupported_response"
        assert payload["error"]["message"] == "quota endpoint is not configured"

    def test_accounts_quota_endpoint_missing_returns_friendly_error(self, tmp_path: Path) -> None:
        manager = manager_for(tmp_path)
        prepare_quota_account(manager)
        assert run(["config", "set", "experimental_private_api.enabled", "true"], manager) == 0
        assert run(["config", "set", "experimental_private_api.quota_enabled", "true"], manager) == 0
        assert run(["config", "set", "experimental_private_api.provider", "custom"], manager) == 0
        assert run(["config", "set", "experimental_private_api.quota_endpoint", ""], manager) == 0
        manager.stdout.seek(0)
        manager.stdout.truncate(0)

        assert run(["accounts", "quota", "work"], manager) == 2

        output = manager.stdout.getvalue()
        assert "ERROR: realtime quota is not configured." in output
        assert "Traceback" not in output
        assert "secret-token" not in output

    def test_accounts_list_all_with_missing_endpoint_marks_account_error(self, tmp_path: Path) -> None:
        manager = manager_for(tmp_path)
        prepare_quota_account(manager)
        assert run(["accounts", "init", "empty"], manager) == 0
        assert run(["config", "set", "experimental_private_api.enabled", "true"], manager) == 0
        assert run(["config", "set", "experimental_private_api.quota_enabled", "true"], manager) == 0
        assert run(["config", "set", "experimental_private_api.provider", "custom"], manager) == 0
        assert run(["config", "set", "experimental_private_api.quota_endpoint", ""], manager) == 0
        manager.stdout.seek(0)
        manager.stdout.truncate(0)

        assert run(["accounts", "list", "-a"], manager) == 0

        output = manager.stdout.getvalue()
        assert "acct_work" in output
        assert "not-configured" in output
        assert "no-auth" in output
        assert "Traceback" not in output

    def test_quota_provider_errors_are_friendly_and_redacted(self, tmp_path: Path) -> None:
        provider = MockPrivateApiProvider()
        manager = manager_for(tmp_path, private_api_provider=provider)
        prepare_quota_account(manager)
        assert run(["config", "set", "experimental_private_api.enabled", "true"], manager) == 0
        assert run(["config", "set", "experimental_private_api.quota_enabled", "true"], manager) == 0

        provider.quota_error = PrivateApiUnsupportedResponseError("quota endpoint is not configured")
        manager.stdout.seek(0)
        manager.stdout.truncate(0)
        assert run(["quota"], manager) == 2
        assert "ERROR: realtime quota is not configured." in manager.stdout.getvalue()

        provider.quota_error = PrivateApiNetworkError("timeout authorization bearer secret-token cookie secret-cookie")
        manager.stdout.seek(0)
        manager.stdout.truncate(0)
        assert run(["quota"], manager) == 2
        output = manager.stdout.getvalue()
        assert "ERROR: realtime quota request failed." in output
        assert "Traceback" not in output
        assert "secret-token" not in output
        assert "secret-cookie" not in output
        assert "authorization" not in output.lower()
        assert "cookie" not in output.lower()

    def test_quota_unauthorized_is_friendly_without_traceback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        manager = manager_for(tmp_path)
        prepare_quota_account(manager)
        assert run(["config", "set", "experimental_private_api.enabled", "true"], manager) == 0
        assert run(["config", "set", "experimental_private_api.quota_enabled", "true"], manager) == 0

        def fake_urlopen(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        manager.stdout.seek(0)
        manager.stdout.truncate(0)

        assert run(["quota"], manager) == 2

        output = manager.stdout.getvalue()
        assert "ERROR: realtime quota authentication failed." in output
        assert "run codex login or an official codex command to refresh auth" in output
        assert "Traceback" not in output
        assert "secret-token" not in output

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
