# Changelog

All notable changes to `codex-workspaces` will be documented in this file.

This project follows a simple changelog format while it is still pre-release.

## 0.2.1 - 2026-07-04

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
- Keeps login-temp account login flows deferred instead of mixing interactive login into migration.
- Saves live `auth.json` before account or workspace switches when an active account is configured.
- Uses a lock file under `~/.codex-workspaces/lock` for account and workspace switching.
- Limits workspace names to letters, numbers, dots, underscores, and hyphens.
