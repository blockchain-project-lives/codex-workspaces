# codex-workspaces

English | [简体中文](README.zh-CN.md) | [Changelog](CHANGELOG.md)

`codex-workspaces` switches between multiple Codex workspace directories under `~/.codex-workspaces/workspaces/` and points the active `~/.codex` path at the selected workspace.

The project now has two entry points:

- A cross-platform Python 3 CLI for Linux, macOS, and Windows.
- The original macOS Bash script, still kept at [`macos/codex-workspaces`](macos/codex-workspaces), for users who installed the shell workflow directly.

On macOS, the Python CLI preserves the original app workflow: stop Codex App, switch the workspace link, and start Codex App again. On Linux and Windows, app control is skipped and the CLI only switches the workspace link.

## Features

- Manage workspace directories such as `~/.codex-workspaces/workspaces/work`.
- Manage account snapshots under `~/.codex-workspaces/accounts/`.
- Switch the active `~/.codex` symlink or directory link.
- Initialize workspace directories with metadata.
- Add a new account through an isolated temporary login workspace with `accounts add --login`.
- Temporarily switch the current workspace account with `accounts use`, then restore the workspace default account.
- Best-effort parse local `auth.json` metadata such as email/account identifiers without network calls.
- Export and import account snapshot backups with explicit warnings for credential-bearing archives.
- Inspect workspace/account metadata and run account-focused `doctor` diagnostics.
- Migrate older `~/.codex-<name>` workspaces and import legacy `~/.codex-accounts` auth snapshots.
- Keep macOS Codex App stop/start/restart support.
- Show local token usage stats from Codex `state_*.sqlite` in read-only mode, including daily, model, workspace, account, JSON, and Markdown views.
- Block unsafe operations from a detected Codex built-in terminal when they cannot be delegated safely.
- Support English and Chinese output through `CODEX_WORKSPACES_LANG`.
- Package as a Python project with tests, CI, and PyPI publishing workflow.

## Requirements

- Python 3.9 or newer for the Python CLI.
- macOS is required only for Codex App control commands: `start`, `stop`, and `restart`.
- Linux and macOS use directory symlinks.
- Windows uses directory symlinks when available and falls back to directory junctions.

## Install

Install the Python CLI from PyPI:

```bash
python3 -m pip install codex-workspaces
```

For isolated CLI installs, `pipx` is recommended:

```bash
pipx install codex-workspaces
```

Install from a local checkout for development:

```bash
python3 -m pip install -e ".[dev]"
```

The legacy macOS shell installer is still available from the `macos/` directory:

```bash
tmp="$(mktemp -t codex-workspaces.XXXXXX)" && curl -fsSL https://raw.githubusercontent.com/blockchain-project-lives/codex-workspaces/main/macos/codex-workspaces -o "$tmp" && bash "$tmp" install && rm -f "$tmp"
```

## Workspace Layout

Default layout:

```text
~/.codex           -> active workspace link
~/.codex-workspaces/
  config.json
  state.json
  lock
  workspaces/
    work/
      auth.json
      .codex-workspace.json
    personal/
      auth.json
      .codex-workspace.json
  accounts/
    acct_work/
      auth.json
      meta.json
```

Customize paths with `CODEX_WORKSPACES_LINK` and `CODEX_WORKSPACES_ROOT`.

## Usage

Initialize workspaces:

```bash
codex-workspaces init personal
codex-workspaces init work
```

Migrate older `~/.codex-<name>` directories without deleting the originals:

```bash
codex-workspaces migrate --dry-run
codex-workspaces migrate
```

If your current `~/.codex` is a real directory instead of a link, migrate it into a named workspace explicitly:

```bash
codex-workspaces init personal --migrate-current
```

Set up account snapshots after Codex has created an `auth.json` in the active workspace:

```bash
codex-workspaces use work --no-stop --no-start
codex-workspaces accounts save work
codex-workspaces accounts set-default work acct_work --activate
codex-workspaces accounts list
```

Standalone accounts can be initialized and later populated from the current workspace `auth.json`:

```bash
codex-workspaces accounts init research
codex-workspaces accounts save research
```

To add a new account without logging out of the current workspace account, use a temporary login workspace:

```bash
codex-workspaces accounts add research --login
```

This switches `~/.codex` to a temporary `login-<account>` workspace, lets you log in, saves the generated `auth.json` as `acct_<account>`, and restores the previous workspace. If login is interrupted, clean stale temporary workspaces with `codex-workspaces accounts cleanup-login-temp`.

Account metadata such as `email`, `account_id`, `user_id`, `organization_id`, and `plan` is parsed from local `auth.json` on a best-effort basis. Parsing failures are ignored, sensitive token-like fields are never printed, and no private API or network request is used.

Export or import account backups:

```bash
codex-workspaces accounts export accounts.tar.gz --all
codex-workspaces accounts export accounts-with-auth.tar.gz --all --include-auth --yes
codex-workspaces accounts import accounts-with-auth.tar.gz --dry-run
codex-workspaces accounts import accounts-with-auth.tar.gz --rename-conflicts
```

Backups created with `--include-auth` contain Codex credentials from `auth.json`. Store them securely and never commit them to git. Without `--include-auth`, export writes metadata only.

Import legacy `codex-accounts` AUTH-mode snapshots:

```bash
codex-workspaces accounts import-legacy ~/.codex-accounts
```

Switch workspaces:

```bash
codex-workspaces work
codex-workspaces use personal
codex-workspaces switch work
```

On macOS, switching stops and restarts Codex App by default. Skip those steps when needed:

```bash
codex-workspaces work --no-stop --no-start
```

Inspect workspaces:

```bash
codex-workspaces list
codex-workspaces current
codex-workspaces info work
codex-workspaces doctor
codex-workspaces stats
codex-workspaces stats summary --days 30
codex-workspaces stats daily --format markdown
codex-workspaces stats models --format json
codex-workspaces stats workspaces
codex-workspaces stats accounts
codex-workspaces stats work --days 14
```

`stats` only reads local SQLite state. Missing or unrecognized workspace/account/model data is shown as `unknown`; results are best-effort and depend on the local Codex files available in each workspace.

Switch accounts within the current workspace:

```bash
codex-workspaces accounts use acct_personal
codex-workspaces accounts restore-default
```

`accounts use` only changes the current workspace `active_account_id`; it does not change `default_account_id`. Entering a workspace uses `CODEX_WORKSPACES_RESTORE_POLICY`: `workspace-default` restores the workspace default account, `last-active` restores that workspace's last active account, and `keep-current` keeps the account you were just using when possible.

`auth.json` contains credentials. Do not commit workspace directories, account snapshots, SQLite state, sessions, or shell snapshots to git; the project `.gitignore` excludes these patterns for local checkouts.

Manage account metadata and snapshots:

```bash
codex-workspaces accounts note acct_research "lab account"
codex-workspaces accounts info acct_research
codex-workspaces accounts rename acct_research acct_lab
codex-workspaces accounts delete acct_lab --force
```

Account deletion always requires `--force` and refuses to delete an account that is still configured as any workspace's default account.

Manage workspace metadata and lifecycle:

```bash
codex-workspaces note work "main paid workspace"
codex-workspaces rename work main
codex-workspaces delete old-workspace --force
```

Control Codex App on macOS:

```bash
codex-workspaces stop
codex-workspaces start
codex-workspaces restart
```

`stats` only reads local SQLite files such as `state_*.sqlite` or `sqlite/state_*.sqlite`. It does not call quota or refresh private APIs.

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `CODEX_APP_NAME` | `Codex` | macOS app name to control. |
| `CODEX_QUIT_TIMEOUT` | `20` | Seconds to wait for app exit. |
| `CODEX_WORKSPACES_LINK` | `$HOME/.codex` | Active workspace link path. |
| `CODEX_WORKSPACES_ROOT` | `$HOME/.codex-workspaces` | Managed root for workspaces, accounts, backups, and lock file. |
| `CODEX_WORKSPACES_RESTORE_POLICY` | `workspace-default` | Account restore policy when entering a workspace: `workspace-default`, `last-active`, or `keep-current`. |
| `CODEX_WORKSPACES_LANG` | auto | Force output language with `en` or `zh`. |

Only the `CODEX_WORKSPACES_*` variables are used for workspace-specific configuration.

## Development

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest
python3 -m build
python3 -m twine check dist/*
```

Design, test, and release notes live in [`docs/`](docs/).

## Publishing

CI runs tests on Linux, macOS, and Windows across Python 3.9, 3.11, and 3.13. The `Publish to TestPyPI` workflow runs for `v*` tags, and the `Publish to PyPI` workflow publishes from `release/v*` branches or manual runs.

Configure TestPyPI and PyPI Trusted Publishing for this repository before using the release workflows. See [`docs/RELEASE.zh-CN.md`](docs/RELEASE.zh-CN.md).
