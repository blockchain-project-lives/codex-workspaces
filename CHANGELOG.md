# Changelog

All notable changes to `codex-accounts` will be documented in this file.

This project follows a simple changelog format while it is still pre-release.

## Unreleased

### Added

- Added the `codex-accounts` macOS shell script for managing multiple Codex account directories.
- Added account listing with active account detection.
- Added current account inspection.
- Added account switching through the active `~/.codex` symlink.
- Added Codex app stop, start, and restart commands.
- Added account creation with `codex-accounts create <account>`.
- Added first-time migration with `codex-accounts create <account> --migrate-current`.
- Added `--migrate` as a short alias for `--migrate-current`.
- Added self-install support with `codex-accounts install [directory]`.
- Added English and Chinese command output, controlled by system language or `CODEX_ACCOUNTS_LANG`.
- Kept legacy `CODEX_ACCOUNT_*` environment variable aliases for compatibility after renaming the command to `codex-accounts`.
- Added English and Simplified Chinese README files.

### Safety

- Refuses to switch accounts when `~/.codex` exists but is not a symlink.
- Delegates stop, switch, and restart commands to Terminal.app when they are run from a detected Codex terminal environment.
- Refuses start and migration commands when they are run from a detected Codex terminal environment.
- Refuses first-time migration while the Codex app is still running.
- Refuses first-time migration when the script cannot confirm whether the Codex app is running.
- Refuses first-time migration when the target account directory already exists.
- Limits account names to letters, numbers, dots, underscores, and hyphens.
