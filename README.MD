# codex-accounts

English | [Simplified Chinese](README.zh-CN.md) | [Changelog](CHANGELOG.md)

`codex-accounts` is a small macOS shell utility for switching between multiple Codex app accounts. It keeps each account in its own `~/.codex-<name>` directory and makes the active `~/.codex` path point to the selected account with a symlink.

It can gracefully quit the Codex app, switch the account link, and start Codex again in one command.

For safety, commands that would interrupt the running Codex app are handled carefully when invoked from the built-in Codex terminal: `stop`, `switch`, and `restart` are delegated to Terminal.app, while `start` and migration are blocked.

## Features

- Manage multiple Codex account directories, such as `~/.codex-work` and `~/.codex-personal`.
- Switch the active account by updating the `~/.codex` symlink.
- Quit, start, or restart the Codex macOS app around account switches.
- List accounts and show the current account.
- Create new account directories.
- Migrate an existing first-time `~/.codex` directory into a named account.
- Delegate `stop`, `switch`, and `restart` to Terminal.app when they are run from a detected Codex terminal.
- Refuse `start` and migration from a detected Codex terminal.
- English and Chinese command output, auto-detected from the system language or forced with `CODEX_ACCOUNTS_LANG`.

## Requirements

- macOS.
- Bash.
- The Codex desktop app installed as `Codex`.

If your app name is different, set `CODEX_APP_NAME`.

## Install

Install directly from the `main` branch on a remote machine:

```bash
tmp="$(mktemp -t codex-accounts.XXXXXX)" && curl -fsSL https://raw.githubusercontent.com/blockchain-project-lives/codex-accounts/main/codex-accounts -o "$tmp" && bash "$tmp" install && rm -f "$tmp"
```

By default, the installer chooses a writable directory that is already in `PATH` when possible. You can also choose the destination explicitly:

```bash
tmp="$(mktemp -t codex-accounts.XXXXXX)" && curl -fsSL https://raw.githubusercontent.com/blockchain-project-lives/codex-accounts/main/codex-accounts -o "$tmp" && bash "$tmp" install "$HOME/.local/bin" && rm -f "$tmp"
```

The command downloads the script to a temporary file before running `install` because the installer copies the script file itself.

## Account Layout

`codex-accounts` uses this layout by default:

```text
~/.codex           -> symlink to the active account directory
~/.codex-work      account directory named work
~/.codex-personal  account directory named personal
```

You can customize the active link and account directory prefix with environment variables.

## First-Time Setup

If you already have a real `~/.codex` directory, migrate it into the account layout before switching:

First open an external system Terminal window and quit Codex:

```bash
codex-accounts stop
```

Then migrate the existing directory:

```bash
codex-accounts create personal --migrate-current
```

This moves the existing `~/.codex` directory to `~/.codex-personal`, then creates a new `~/.codex` symlink pointing to it.

The migration command refuses to run while the Codex app is still running. If it cannot confirm whether Codex is running, it refuses to migrate as well. This avoids moving configuration files while the app may still be reading or writing them.

Do not run migration from the built-in Codex terminal. The script checks common Codex environment markers, such as `CODEX_SHELL`, `CODEX_SANDBOX`, `CODEX_THREAD_ID`, and the Codex app bundle identifier, and blocks migration when those markers are present.

Then create another account directory when needed:

```bash
codex-accounts create work
```

Switch to it:

```bash
codex-accounts work
```

If `~/.codex` exists and is not a symlink, `codex-accounts` refuses to switch accounts so it does not accidentally remove your existing data.

## Usage

The built-in Codex terminal can run safe commands such as `list`, `current`, and normal `create`. If you run `stop`, `switch`, or `restart` from the built-in Codex terminal, the script opens Terminal.app and runs the command there. Run `start` and migration from an external Terminal yourself.

List accounts:

```bash
codex-accounts list
```

Show the current account:

```bash
codex-accounts current
```

Create an account:

```bash
codex-accounts create work
```

Migrate the existing real `~/.codex` directory into a new account:

```bash
codex-accounts stop
codex-accounts create personal --migrate-current
```

Switch accounts:

```bash
codex-accounts work
codex-accounts use personal
codex-accounts switch work
```

Switch without starting Codex afterwards:

```bash
codex-accounts personal --no-start
```

Switch without stopping Codex first:

```bash
codex-accounts work --no-stop
```

Force quit Codex if it does not exit before the timeout:

```bash
codex-accounts work --force
```

Control the Codex app:

```bash
codex-accounts stop
codex-accounts start
codex-accounts restart
```

Show help:

```bash
codex-accounts help
```

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `CODEX_APP_NAME` | `Codex` | macOS app name to quit/start. |
| `CODEX_QUIT_TIMEOUT` | `20` | Seconds to wait for the app to quit. |
| `CODEX_ACCOUNTS_LINK` | `$HOME/.codex` | Active account symlink path. |
| `CODEX_ACCOUNTS_PREFIX` | `$HOME/.codex-` | Prefix for account directories. |
| `CODEX_ACCOUNTS_LANG` | auto | Force output language with `en` or `zh`. |

Legacy aliases `CODEX_ACCOUNT_LINK`, `CODEX_ACCOUNT_PREFIX`, and `CODEX_ACCOUNT_LANG` are still honored for compatibility.

Example:

```bash
CODEX_ACCOUNTS_LANG=en codex-accounts list
CODEX_APP_NAME="Codex" codex-accounts restart
```

## Notes

- Account names may contain letters, numbers, dots, underscores, and hyphens.
- `stop`, `switch`, and `restart` are delegated to Terminal.app when the script detects a Codex terminal environment.
- `start` and `create <account> --migrate-current` are blocked when the script detects a Codex terminal environment.
- `create <account> --migrate-current` only works when Codex is confirmed not running, `~/.codex` exists as a real directory, and `~/.codex-<account>` does not already exist.
- Switching removes and recreates only the `~/.codex` symlink.
- Account directories such as `~/.codex-work` are not deleted by switch commands.
- The script is dependency-light and uses standard macOS commands such as `osascript`, `open`, `pgrep`, and `killall`.
