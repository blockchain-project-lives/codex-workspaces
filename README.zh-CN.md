# codex-workspaces

[English](README.MD) | 简体中文 | [更新日志](CHANGELOG.md)

`codex-workspaces` 用来切换多个 Codex 工作区目录。默认把每个工作区放在 `~/.codex-workspaces/workspaces/` 下，并让当前生效的 `~/.codex` 指向选中的工作区。

项目现在有两个入口：

- 跨平台 Python 3 CLI，支持 Linux、macOS 和 Windows。
- 原来的 macOS Bash 脚本，仍保留在 [`codex-workspaces`](macos/codex-workspaces)，兼容已经使用 shell 安装方式的用户。

在 macOS 上，Python CLI 保留原来的 App 流程：关闭 Codex App、切换工作区链接、再启动 Codex App。在 Linux 和 Windows 上，CLI 会跳过 App 启停，只切换工作区链接。

## 功能

- 管理 `~/.codex-workspaces/workspaces/work` 这类工作区目录。
- 管理 `~/.codex-workspaces/accounts/` 下的账号快照。
- 切换当前 `~/.codex` 软链接或目录链接。
- 初始化带元数据的工作区目录。
- 通过隔离的临时登录工作区执行 `accounts add --login`，新增账号而不退出当前账号。
- 通过 `accounts use` 临时切换当前工作区账号，并可恢复工作区默认账号。
- 从本地 `auth.json` best-effort 解析 email/account 等元信息，不发网络请求。
- 导出和导入账号快照备份包；包含 auth 的备份会明确提示凭据风险。
- 查看工作区/账号元数据，并通过 `doctor` 做账号诊断。
- 迁移旧版 `~/.codex-<name>` 工作区，并导入旧 `~/.codex-accounts` 账号快照。
- 保留 macOS 上 Codex App 的 `stop`、`start`、`restart`。
- 以只读方式读取 Codex `state_*.sqlite`，展示每日、模型、工作区、账号、JSON 和 Markdown 形式的本地 token 统计。
- 在检测到 Codex 内置 Terminal 且无法安全转交时，阻止危险操作。
- 支持中英文输出，可通过 `CODEX_WORKSPACES_LANG` 指定。
- 提供 Python 包、测试、GitHub CI 和 PyPI 发布工作流。

## 要求

- Python 3.9 或更新版本。
- 只有 `start`、`stop`、`restart` 这类 Codex App 控制命令要求 macOS。
- Linux 和 macOS 使用目录软链接。
- Windows 优先使用目录软链接，不可用时回退到目录 junction。

## 安装

从 PyPI 安装 Python CLI：

```bash
python3 -m pip install codex-workspaces
```

推荐用 `pipx` 做隔离安装：

```bash
pipx install codex-workspaces
```

本地开发安装：

```bash
python3 -m pip install -e ".[dev]"
```

旧版 macOS shell 安装方式仍可从 `macos/` 目录使用：

```bash
tmp="$(mktemp -t codex-workspaces.XXXXXX)" && curl -fsSL https://raw.githubusercontent.com/blockchain-project-lives/codex-workspaces/main/macos/codex-workspaces -o "$tmp" && bash "$tmp" install && rm -f "$tmp"
```

## 工作区目录

默认布局：

```text
~/.codex           -> 当前工作区链接
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

可通过 `CODEX_WORKSPACES_LINK` 和 `CODEX_WORKSPACES_ROOT` 自定义路径。

## 使用

初始化工作区：

```bash
codex-workspaces init personal
codex-workspaces init work
```

迁移旧版 `~/.codex-<name>` 目录，不会自动删除旧目录：

```bash
codex-workspaces migrate --dry-run
codex-workspaces migrate
```

如果当前 `~/.codex` 是真实目录而不是链接，需要显式迁移成一个命名工作区：

```bash
codex-workspaces init personal --migrate-current
```

在 Codex 已经在当前工作区写入 `auth.json` 后，设置账号快照：

```bash
codex-workspaces use work --no-stop --no-start
codex-workspaces accounts save work
codex-workspaces accounts set-default work acct_work --activate
codex-workspaces accounts list
```

单独账号可以先初始化，再从当前工作区的 `auth.json` 保存进去：

```bash
codex-workspaces accounts init research
codex-workspaces accounts save research
```

如果要新增账号，但不想退出当前工作区账号，可以用临时登录工作区：

```bash
codex-workspaces accounts add research --login
```

它会把 `~/.codex` 临时切到 `login-<账号>` 工作区，让你登录新账号；登录生成 `auth.json` 后保存为 `acct_<账号>`，再恢复原工作区。如果登录中断，可用 `codex-workspaces accounts cleanup-login-temp` 清理残留临时工作区。

账号元信息如 `email`、`account_id`、`user_id`、`organization_id`、`plan` 会从本地 `auth.json` best-effort 解析。解析失败不会影响账号功能，不会打印 token/secret/cookie 等敏感字段，也不会调用私有接口或发起网络请求。

导出或导入账号备份：

```bash
codex-workspaces accounts export accounts.tar.gz --all
codex-workspaces accounts export accounts-with-auth.tar.gz --all --include-auth --yes
codex-workspaces accounts import accounts-with-auth.tar.gz --dry-run
codex-workspaces accounts import accounts-with-auth.tar.gz --rename-conflicts
```

使用 `--include-auth` 创建的备份包包含 `auth.json` 里的 Codex 凭据，必须安全保存，不能提交到 git。不带 `--include-auth` 时只导出 meta，不包含 auth。

导入旧 `codex-accounts` 的 AUTH 模式账号快照：

```bash
codex-workspaces accounts import-legacy ~/.codex-accounts
```

切换工作区：

```bash
codex-workspaces work
codex-workspaces use personal
codex-workspaces switch work
```

macOS 上默认会在切换前后关闭和启动 Codex App。需要时可以跳过：

```bash
codex-workspaces work --no-stop --no-start
```

查看工作区：

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

`stats` 只读取本地 SQLite 状态文件。无法识别的 workspace/account/model 会显示为 `unknown`；统计结果取决于各工作区里实际存在的 Codex 本地文件。

在当前工作区临时切换账号：

```bash
codex-workspaces accounts use acct_personal
codex-workspaces accounts restore-default
```

`accounts use` 只修改当前工作区的 `active_account_id`，不会修改 `default_account_id`。进入工作区时按 `CODEX_WORKSPACES_RESTORE_POLICY` 恢复账号：`workspace-default` 恢复工作区默认账号，`last-active` 恢复该工作区上次活跃账号，`keep-current` 尽量沿用刚才正在使用的账号。

`auth.json` 包含认证凭据，不要提交到 git。工作区目录、账号快照、SQLite 状态、sessions 和 shell snapshots 已在本项目 `.gitignore` 中排除。

管理账号备注和快照生命周期：

```bash
codex-workspaces accounts note acct_research "实验室账号"
codex-workspaces accounts info acct_research
codex-workspaces accounts rename acct_research acct_lab
codex-workspaces accounts delete acct_lab --force
```

删除账号始终需要 `--force`；如果某个工作区仍把该账号设为默认账号，删除会被拒绝。

管理工作区备注和生命周期：

```bash
codex-workspaces note work "主力付费工作区"
codex-workspaces rename work main
codex-workspaces delete old-workspace --force
```

macOS 上控制 Codex App：

```bash
codex-workspaces stop
codex-workspaces start
codex-workspaces restart
```

`stats` 只读取本地 SQLite 文件，例如 `state_*.sqlite` 或 `sqlite/state_*.sqlite`。它不会调用 quota 或 refresh 私有接口。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CODEX_APP_NAME` | `Codex` | macOS App 名称。 |
| `CODEX_QUIT_TIMEOUT` | `20` | 等待 App 退出的秒数。 |
| `CODEX_WORKSPACES_LINK` | `$HOME/.codex` | 当前工作区链接路径。 |
| `CODEX_WORKSPACES_ROOT` | `$HOME/.codex-workspaces` | workspaces、accounts、backups 和 lock 所在管理根目录。 |
| `CODEX_WORKSPACES_RESTORE_POLICY` | `workspace-default` | 进入工作区时的账号恢复策略：`workspace-default`、`last-active` 或 `keep-current`。 |
| `CODEX_WORKSPACES_LANG` | 自动 | 强制输出语言，可设为 `en` 或 `zh`。 |

工作区相关配置只使用 `CODEX_WORKSPACES_*` 变量。

## 开发

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest
python3 -m build
python3 -m twine check dist/*
```

设计、测试和发布说明见 [`docs/`](docs/)。

## 发布

CI 会在 Linux、macOS、Windows 上测试 Python 3.9、3.11、3.13。`Publish to TestPyPI` 工作流会在 `v*` tag 上运行，`Publish to PyPI` 工作流支持从 `release/v*` 分支或手动触发发布。

发布前需要在 TestPyPI 和 PyPI 分别配置 Trusted Publishing。详见 [`docs/RELEASE.zh-CN.md`](docs/RELEASE.zh-CN.md)。
