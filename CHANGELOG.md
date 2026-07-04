# Changelog

All notable changes to `codex-workspaces` will be documented in this file.

This project follows a simple changelog format while it is still pre-release.

## Unreleased

### Added

- Added the cross-platform Python 3 package with the `codex-workspaces` console script.
- Added Linux/macOS symlink switching and Windows symlink/junction support.
- Added Python unit tests for workspace validation, creation, migration, switching, CLI dispatch, and platform safety behavior.
- Added `doctor` diagnostics for platform, path, link, and Codex terminal status.
- Added workspace size and modified-time metadata to `list`.
- Added workspace `rename`, guarded `delete --force`, and local `note` management.
- Added `pyproject.toml`, package metadata, editable install support, and PyPI-ready build configuration.
- Added GitHub Actions CI for Linux, macOS, Windows, and Python 3.9/3.11/3.13.
- Added GitHub Actions Trusted Publishing workflow for PyPI releases.
- Added design, testing, and release documentation under `docs/`.
- Added the `codex-workspaces` macOS shell script for managing multiple Codex workspace directories.
- Added workspace listing with active workspace detection.
- Added current workspace inspection.
- Added workspace switching through the active `~/.codex` symlink.
- Added Codex app stop, start, and restart commands.
- Added workspace creation with `codex-workspaces create <workspace>`.
- Added first-time migration with `codex-workspaces create <workspace> --migrate-current`.
- Added `--migrate` as a short alias for `--migrate-current`.
- Added self-install support with `codex-workspaces install [directory]`.
- Added English and Chinese command output, controlled by system language or `CODEX_WORKSPACES_LANG`.
- Renamed the command, package, module, docs, and environment variables to `codex-workspaces` / `CODEX_WORKSPACES_*`.
- Added English and Simplified Chinese README files.

### Safety

- Refuses to switch workspaces when `~/.codex` exists but is not a symlink.
- Delegates stop, switch, and restart commands to Terminal.app when they are run from a detected Codex terminal environment.
- Refuses start and migration commands when they are run from a detected Codex terminal environment.
- Refuses first-time migration while the Codex app is still running.
- Refuses first-time migration when the script cannot confirm whether the Codex app is running.
- Refuses first-time migration when the target workspace directory already exists.
- Limits workspace names to letters, numbers, dots, underscores, and hyphens.
