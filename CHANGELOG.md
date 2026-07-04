# Changelog

All notable changes to `codex-account` will be documented in this file.

This project follows a simple changelog format while it is still pre-release.

## Unreleased

### Added

- Added the `codex-account` macOS shell script for managing multiple Codex account directories.
- Added account listing with active account detection.
- Added current account inspection.
- Added account switching through the active `~/.codex` symlink.
- Added Codex app stop, start, and restart commands.
- Added account creation with `codex-account create <account>`.
- Added first-time migration with `codex-account create <account> --migrate-current`.
- Added `--migrate` as a short alias for `--migrate-current`.
- Added self-install support with `codex-account install [directory]`.
- Added English and Chinese command output, controlled by system language or `CODEX_ACCOUNT_LANG`.
- Added English and Simplified Chinese README files.

### Safety

- Refuses to switch accounts when `~/.codex` exists but is not a symlink.
- Refuses first-time migration while the Codex app is still running.
- Refuses first-time migration when the script cannot confirm whether the Codex app is running.
- Refuses first-time migration when the target account directory already exists.
- Limits account names to letters, numbers, dots, underscores, and hyphens.
