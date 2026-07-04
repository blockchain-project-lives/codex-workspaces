# codex-workspaces

English | [简体中文](README.zh-CN.md) | [Changelog](CHANGELOG.md)

`codex-workspaces` switches between multiple Codex workspace directories by keeping each workspace in `~/.codex-<name>` and pointing the active `~/.codex` path at the selected workspace.

The project now has two entry points:

- A cross-platform Python 3 CLI for Linux, macOS, and Windows.
- The original macOS Bash script, still kept at [`macos/codex-workspaces`](macos/codex-workspaces), for users who installed the shell workflow directly.

On macOS, the Python CLI preserves the original app workflow: stop Codex App, switch the workspace link, and start Codex App again. On Linux and Windows, app control is skipped and the CLI only switches the workspace link.

## Features

- Manage workspace directories such as `~/.codex-work` and `~/.codex-personal`.
- Switch the active `~/.codex` symlink or directory link.
- Create workspace directories and migrate an existing real `~/.codex` directory.
- Keep macOS Codex App stop/start/restart support.
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
~/.codex-work      workspace directory named work
~/.codex-personal  workspace directory named personal
```

Customize paths with `CODEX_WORKSPACES_LINK` and `CODEX_WORKSPACES_PREFIX`.

## Usage

Create workspaces:

```bash
codex-workspaces create personal
codex-workspaces create work
```

If you already have a real `~/.codex` directory, migrate it first:

```bash
codex-workspaces create personal --migrate-current
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
codex-workspaces doctor
```

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

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `CODEX_APP_NAME` | `Codex` | macOS app name to control. |
| `CODEX_QUIT_TIMEOUT` | `20` | Seconds to wait for app exit. |
| `CODEX_WORKSPACES_LINK` | `$HOME/.codex` | Active workspace link path. |
| `CODEX_WORKSPACES_PREFIX` | `$HOME/.codex-` | Workspace directory prefix. |
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

CI runs tests on Linux, macOS, and Windows across Python 3.9, 3.11, and 3.13. The `Publish to PyPI` workflow builds and publishes distributions when a GitHub Release is published or the workflow is run manually.

Configure TestPyPI and PyPI Trusted Publishing for this repository before using the release workflows. See [`docs/RELEASE.zh-CN.md`](docs/RELEASE.zh-CN.md).
