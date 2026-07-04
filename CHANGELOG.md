# Changelog

All notable changes to `codex-workspaces` will be documented in this file.

This project follows a simple changelog format while it is still pre-release.

## 0.3.4 - 2026-07-04

### Added

- Added `accounts current --id` and `accounts current --json` for shell-friendly account composition.

### Changed

- Default experimental private API quota configuration now points at the ChatGPT WHAM usage endpoint while remaining disabled by default.
- Updated WHAM quota parsing for `rate_limit.primary_window`, reset times, window duration, allowed/limit status, and credits metadata.
- Tightened quota auth extraction to use ChatGPT `auth.json` fields (`tokens.access_token`, `tokens.refresh_token`, and `tokens.account_id`) without recursively guessing token fields.
- Updated README and testing docs to document the current `base_url`, `quota_endpoint`, and `account_endpoint` configuration fields.

### Fixed

- Fixed `quota`, `quota --json`, `accounts quota`, and `accounts list -a` so disabled or failing private API quota calls return friendly errors instead of Python tracebacks.
- Fixed `accounts list -a` so one account's realtime quota failure does not interrupt the whole account list.
- Fixed `accounts current` command composition by documenting `accounts info "$(codex-workspaces accounts current --id)"`.
- Fixed WHAM quota requests to include `OpenAI-Account-Id`.
- Fixed unauthorized and expired-access-token quota failures to return actionable login/refresh hints without leaking credentials.

### Security

- Avoids using `id_token` or recursively discovered token-like fields as private API bearer tokens.

## 0.3.3 - 2026-07-04

### Added

- Added best-effort local `auth.json` metadata inspection for account email, account ID, user ID, organization ID, plan, and auth hash without private API calls.
- Added `accounts refresh-meta <account>|--all [--overwrite]` for manually refreshing parsed account metadata.
- Added `accounts export` and `accounts import` backup support with meta-only exports by default, explicit `--include-auth` credential export, dry-run import plans, conflict renaming, overwrite with pre-import backup, auth hash validation, and unsafe tar path rejection.
- Added enhanced local `stats` views for summary, daily usage, top models, workspace aggregation, account aggregation, JSON output, and Markdown output.
- Added experimental private API provider scaffolding for realtime account quota lookup.
- Added `quota` for the current active account and `accounts quota <account>` for a specific managed account.
- Added `accounts list -a` / `accounts list --all-with-quota` for realtime quota display across managed accounts.
- Added `accounts refresh [account|--all]` for refreshing remote account metadata and quota cache without modifying `auth.json`.
- Added `config get/set experimental_private_api.*` for explicitly enabling and configuring experimental private API behavior.
- Added quota cache with TTL and auth-hash invalidation under `~/.codex-workspaces/cache/quota/`.

### Changed

- Improved account list/info metadata enrichment while keeping auth parsing optional and non-blocking.
- Improved local stats presentation while keeping all statistics local and read-only.
- Updated README, design, testing, release, and changelog docs for the 0.3.3 workflow.

### Security

- Added explicit warnings for exports that include `auth.json` credentials.
- Added backup archive validation against path traversal, symlinks, device files, and auth hash mismatches during import.
- Kept auth inspection local-only: no token refresh, no private API calls, no network requests, and no sensitive token-like fields in normal output.
- Private API quota/refresh features are disabled by default and must be explicitly enabled.
- Sensitive auth material is redacted from private API errors, command output, and quota cache.
- Quota cache stores no tokens, cookies, authorization headers, or raw `auth.json`.

### Notes

- Realtime quota and refresh depend on experimental private Codex/OpenAI interfaces and may break when upstream behavior changes.
- `stats` remains local historical usage; `quota` is realtime remote lookup and is intentionally separate.

## 0.3.1 - 2026-07-04

### Added

- Added `accounts add <account> --login` and `accounts login-temp <account>` to create an isolated temporary login workspace, save the new auth snapshot, and restore the previous workspace.
- Added `accounts cleanup-login-temp` for stale temporary login workspaces.
- Added enhanced `accounts list` and `accounts info` output with auth status, current/default markers, workspace references, orphan/active-only status, notes, paths, and auth hashes.
- Added account-focused `doctor` checks for missing account references, workspace auth without defaults, orphan accounts, permission issues, and legacy directory leftovers.
- Added final migration report summaries for migrated workspaces, created accounts, imported accounts, renamed account conflicts, and skipped special files.
- Added `codex-workspaces info <workspace>` for workspace metadata inspection.
- Added `CODEX_WORKSPACES_RESTORE_POLICY` with `workspace-default`, `last-active`, and `keep-current`.

### Changed

- Updated README, design, testing, and release docs for the 0.3.1 account and restore-policy workflow.

## 0.3.0 - 2026-07-04

### Added

- Added the cross-platform Python 3 package with the `codex-workspaces` console script.
- Added Linux/macOS symlink switching and Windows symlink/junction support.
- Added Python unit tests for workspace validation, creation, migration, switching, CLI dispatch, and platform safety behavior.
- Added `doctor` diagnostics for platform, path, link, and Codex terminal status.
- Added workspace size and modified-time metadata to `list`.
- Added workspace `rename`, guarded `delete --force`, and local `note` management.
- Added read-only local SQLite token usage stats with `stats`.
- Added the unified `~/.codex-workspaces/` root with workspace metadata, account snapshots, and default-account restore behavior.
- Added legacy workspace migration with `migrate`, `migrate --dry-run`, and `init <workspace> --migrate-current`.
- Added legacy account import with `accounts import-legacy` and workspace auth import with `accounts import-workspaces`.
- Added account `rename`, guarded `delete --force`, and account `note` management.
- Added `pyproject.toml`, package metadata, editable install support, and PyPI-ready build configuration.
- Added GitHub Actions CI for Linux, macOS, Windows, and Python 3.9/3.11/3.13.
- Added GitHub Actions Trusted Publishing workflow for PyPI releases.
- Added design, testing, and release documentation under `docs/`.
- Added the `codex-workspaces` macOS shell script for managing multiple Codex workspace directories.
- Added workspace listing with active workspace detection.
- Added current workspace inspection.
- Added workspace switching through the active `~/.codex` symlink.
- Added Codex app stop, start, and restart commands.
- Added workspace initialization with `codex-workspaces init <workspace>`.
- Added self-install support with `codex-workspaces install [directory]`.
- Added English and Chinese command output, controlled by system language or `CODEX_WORKSPACES_LANG`.
- Renamed the command, package, module, docs, and environment variables to `codex-workspaces` / `CODEX_WORKSPACES_*`.
- Added English and Simplified Chinese README files.

### Safety

- Refuses to switch workspaces when `~/.codex` exists but is not a symlink.
- Delegates stop, switch, and restart commands to Terminal.app when they are run from a detected Codex terminal environment.
- Refuses start and migration commands when they are run from a detected Codex terminal environment.
- Backs up legacy workspace/account sources before migration and never deletes old directories automatically.
- Keeps migration non-interactive; new account login is handled separately through the login-temp workflow.
- Saves live `auth.json` before account or workspace switches when an active account is configured.
- Uses a lock file under `~/.codex-workspaces/lock` for account and workspace switching.
- Limits workspace names to letters, numbers, dots, underscores, and hyphens.
