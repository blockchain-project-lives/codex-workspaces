# codex-workspaces

English | [Simplified Chinese](README.zh-CN.md) | [Changelog](CHANGELOG.md)

`codex-workspaces` is a small macOS shell utility for switching between multiple Codex app workspaces. It keeps each workspace in its own `~/.codex-<name>` directory and makes the active `~/.codex` path point to the selected workspace with a symlink.

It can gracefully quit the Codex app, switch the workspace link, and start Codex again in one command.

For safety, commands that would interrupt the running Codex app are handled carefully when invoked from the built-in Codex terminal: `stop`, `switch`, and `restart` are delegated to Terminal.app, while `start` and migration are blocked.

## Features

- Manage multiple Codex workspace directories, such as `~/.codex-work` and `~/.codex-personal`.
- Switch the active workspace by updating the `~/.codex` symlink.
- Quit, start, or restart the Codex macOS app around workspace switches.
- List workspaces and show the current workspace.
- Initialize new workspace directories.
- Migrate an existing first-time `~/.codex` directory into a named workspace.
- Delegate `stop`, `switch`, and `restart` to Terminal.app when they are run from a detected Codex terminal.
- Refuse `start` and migration from a detected Codex terminal.
- English and Chinese command output, auto-detected from the system language or forced with `CODEX_WORKSPACES_LANG`.

## Requirements

- macOS.
- Bash.
- The OpenAI desktop app installed as `ChatGPT` or the older `Codex` name.

If your app name is different, set `CODEX_APP_NAME`.

## Install

Install directly from the `main` branch on a remote machine:

```bash
tmp="$(mktemp -t codex-workspaces.XXXXXX)" && curl -fsSL https://raw.githubusercontent.com/blockchain-project-lives/codex-workspaces/main/macos/codex-workspaces -o "$tmp" && bash "$tmp" install && rm -f "$tmp"
```

By default, the installer chooses a writable directory that is already in `PATH` when possible. You can also choose the destination explicitly:

```bash
tmp="$(mktemp -t codex-workspaces.XXXXXX)" && curl -fsSL https://raw.githubusercontent.com/blockchain-project-lives/codex-workspaces/main/macos/codex-workspaces -o "$tmp" && bash "$tmp" install "$HOME/.local/bin" && rm -f "$tmp"
```

The command downloads the script to a temporary file before running `install` because the installer copies the script file itself.

## Workspace Layout

`codex-workspaces` uses this layout by default:

```text
~/.codex           -> symlink to the active workspace directory
~/.codex-work      workspace directory named work
~/.codex-personal  workspace directory named personal
```

You can customize the active link and workspace directory prefix with environment variables.

## First-Time Setup

If you already have a real `~/.codex` directory, migrate it into the workspace layout before switching:

First open an external system Terminal window and quit Codex:

```bash
codex-workspaces stop
```

Then migrate the existing directory:

```bash
codex-workspaces init personal --migrate-current
```

This moves the existing `~/.codex` directory to `~/.codex-personal`, then creates a new `~/.codex` symlink pointing to it.

The migration command refuses to run while the Codex app is still running. If it cannot confirm whether Codex is running, it refuses to migrate as well. This avoids moving configuration files while the app may still be reading or writing them.

Do not run migration from the built-in Codex terminal. The script checks common Codex environment markers, such as `CODEX_SHELL`, `CODEX_SANDBOX`, `CODEX_THREAD_ID`, and the Codex app bundle identifier, and blocks migration when those markers are present.

Then initialize another workspace directory when needed:

```bash
codex-workspaces init work
```

Switch to it:

```bash
codex-workspaces work
```

If `~/.codex` exists and is not a symlink, `codex-workspaces` refuses to switch workspaces so it does not accidentally remove your existing data.

## Usage

The built-in Codex terminal can run safe commands such as `list`, `current`, and normal `init`. If you run `stop`, `switch`, or `restart` from the built-in Codex terminal, the script opens Terminal.app and runs the command there. Run `start` and migration from an external Terminal yourself.

List workspaces:

```bash
codex-workspaces list
```

Show the current workspace:

```bash
codex-workspaces current
```

Initialize a workspace:

```bash
codex-workspaces init work
```

Migrate the existing real `~/.codex` directory into a new workspace:

```bash
codex-workspaces stop
codex-workspaces init personal --migrate-current
```

Switch workspaces:

```bash
codex-workspaces work
codex-workspaces use personal
codex-workspaces switch work
```

Switch without starting Codex afterwards:

```bash
codex-workspaces personal --no-start
```

Switch without stopping Codex first:

```bash
codex-workspaces work --no-stop
```

Force quit Codex if it does not exit within 5 seconds:

```bash
codex-workspaces work --force
```

Control the Codex app:

```bash
codex-workspaces stop
codex-workspaces start
codex-workspaces restart
```

Show help:

```bash
codex-workspaces help
```

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `CODEX_APP_NAME` | auto-detect (`ChatGPT`, fallback `Codex`) | macOS app name to quit/start. |
| `CODEX_QUIT_TIMEOUT` | `20` | Seconds to wait for the app to quit. |
| `CODEX_WORKSPACES_LINK` | `$HOME/.codex` | Active workspace symlink path. |
| `CODEX_WORKSPACES_PREFIX` | `$HOME/.codex-` | Prefix for workspace directories. |
| `CODEX_WORKSPACES_LANG` | auto | Force output language with `en` or `zh`. |


Example:

```bash
CODEX_WORKSPACES_LANG=en codex-workspaces list
CODEX_APP_NAME="Codex" codex-workspaces restart
```

## Notes

- Workspace names may contain letters, numbers, dots, underscores, and hyphens.
- `stop`, `switch`, and `restart` are delegated to Terminal.app when the script detects a Codex terminal environment.
- `start` and `init <workspace> --migrate-current` are blocked when the script detects a Codex terminal environment.
- `init <workspace> --migrate-current` only works when Codex is confirmed not running, `~/.codex` exists as a real directory, and `~/.codex-<workspace>` does not already exist.
- Switching removes and recreates only the `~/.codex` symlink.
- Workspace directories such as `~/.codex-work` are not deleted by switch commands.
- The script is dependency-light and uses standard macOS commands such as `osascript`, `open`, `pgrep`, and `killall`.
