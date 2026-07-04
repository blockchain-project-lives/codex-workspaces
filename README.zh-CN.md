# codex-account

[English](README.MD) | 简体中文 | [更新日志](CHANGELOG.md)

`codex-account` 是一个 macOS Shell 小工具，用来管理和切换多个 Codex App 账号。它会把每个账号保存在独立的 `~/.codex-<账号名>` 目录中，并让当前生效的 `~/.codex` 通过软链接指向选中的账号目录。

切换账号时，它可以自动关闭 Codex App、切换 `~/.codex` 软链接，然后重新启动 Codex App。

## 功能

- 管理多个 Codex 账号目录，例如 `~/.codex-work` 和 `~/.codex-personal`。
- 通过更新 `~/.codex` 软链接切换当前账号。
- 在切换账号前后关闭、启动或重启 Codex macOS App。
- 查看账号列表和当前账号。
- 创建新的账号目录。
- 将首次使用时已有的 `~/.codex` 真实目录迁移成指定账号。
- 支持英文和中文命令输出，可根据系统语言自动判断，也可通过 `CODEX_ACCOUNT_LANG` 强制指定。

## 要求

- macOS。
- Bash。
- 已安装 Codex 桌面 App，默认 App 名称为 `Codex`。

如果你的 App 名称不同，可以设置 `CODEX_APP_NAME`。

## 安装

在远程机器上从 `main` 分支直接安装：

```bash
tmp="$(mktemp -t codex-account.XXXXXX)" && curl -fsSL https://raw.githubusercontent.com/blockchain-project-lives/codex-account/main/codex-account -o "$tmp" && bash "$tmp" install && rm -f "$tmp"
```

默认情况下，安装器会尽量选择一个已经在 `PATH` 中且可写的目录。你也可以显式指定安装目录：

```bash
tmp="$(mktemp -t codex-account.XXXXXX)" && curl -fsSL https://raw.githubusercontent.com/blockchain-project-lives/codex-account/main/codex-account -o "$tmp" && bash "$tmp" install "$HOME/.local/bin" && rm -f "$tmp"
```

这里先把脚本下载到临时文件，再执行 `install`，是因为安装逻辑需要复制脚本文件自身。

## 账号目录约定

`codex-account` 默认使用下面的目录结构：

```text
~/.codex           -> 指向当前账号目录的软链接
~/.codex-work      名为 work 的账号目录
~/.codex-personal  名为 personal 的账号目录
```

你可以通过环境变量自定义当前账号链接和账号目录前缀。

## 首次设置

如果你已经有一个真实存在的 `~/.codex` 目录，请先把它迁移到账户目录结构中：

先在外部 Terminal 窗口关闭 Codex：

```bash
codex-account stop
```

然后迁移已有目录：

```bash
codex-account create personal --migrate-current
```

这个命令会把已有的 `~/.codex` 目录移动到 `~/.codex-personal`，然后重新创建 `~/.codex` 软链接指向它。

迁移命令会在 Codex App 仍在运行时拒绝执行；如果无法确认 Codex 是否正在运行，也会拒绝迁移。这样可以避免 App 正在读写配置文件时移动目录。

之后如果需要，可以再创建另一个账号目录：

```bash
codex-account create work
```

切换到该账号：

```bash
codex-account work
```

如果 `~/.codex` 已存在且不是软链接，`codex-account` 会拒绝切换账号，避免误删或覆盖你的已有数据。

## 使用方法

查看账号列表：

```bash
codex-account list
```

查看当前账号：

```bash
codex-account current
```

创建账号：

```bash
codex-account create work
```

将已有的真实 `~/.codex` 目录迁移成新账号：

```bash
codex-account stop
codex-account create personal --migrate-current
```

切换账号：

```bash
codex-account work
codex-account use personal
codex-account switch work
```

切换后不启动 Codex：

```bash
codex-account personal --no-start
```

切换前不关闭 Codex：

```bash
codex-account work --no-stop
```

如果 Codex 在超时前没有退出，则强制关闭：

```bash
codex-account work --force
```

控制 Codex App：

```bash
codex-account stop
codex-account start
codex-account restart
```

显示帮助：

```bash
codex-account help
```

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CODEX_APP_NAME` | `Codex` | 要关闭/启动的 macOS App 名称。 |
| `CODEX_QUIT_TIMEOUT` | `20` | 等待 App 退出的秒数。 |
| `CODEX_ACCOUNT_LINK` | `$HOME/.codex` | 当前账号软链接路径。 |
| `CODEX_ACCOUNT_PREFIX` | `$HOME/.codex-` | 账号目录前缀。 |
| `CODEX_ACCOUNT_LANG` | 自动判断 | 强制输出语言，可设为 `en` 或 `zh`。 |

示例：

```bash
CODEX_ACCOUNT_LANG=zh codex-account list
CODEX_APP_NAME="Codex" codex-account restart
```

## 注意事项

- 账号名只能包含字母、数字、点、下划线和连字符。
- `create <账号名> --migrate-current` 只会在确认 Codex 未运行、`~/.codex` 是真实目录且 `~/.codex-<账号名>` 不存在时执行迁移。
- 切换账号时只会删除并重建 `~/.codex` 这个软链接。
- `~/.codex-work` 这类账号目录不会被切换命令删除。
- 脚本依赖很少，主要使用 macOS 自带命令，例如 `osascript`、`open`、`pgrep` 和 `killall`。
