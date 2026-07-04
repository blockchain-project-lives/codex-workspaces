# codex-workspaces Python 版设计文档

## 背景

原项目是 macOS Bash 脚本，核心能力是切换 `~/.codex` 到不同工作区目录。脚本还负责关闭、启动 Codex App，并在 Codex 内置 Terminal 中把危险命令转交给 Terminal.app。

Python 版的目标是在保留 shell 脚本的情况下，提供可测试、可打包、可在 Linux/macOS/Windows 使用的 CLI。

## 目标

- 保持现有命令语义：`list`、`current`、`init`、`stats`、`switch/use`、工作区名快捷切换、`stop/start/restart`。
- 工作区目录管理跨平台可用。
- macOS App 控制保留原行为。
- 非 macOS 平台不执行 App 启停，只切换工作区链接。
- 支持 PyPI 安装和 `python -m codex_workspaces`。
- 核心逻辑可单元测试，不依赖真实 Codex App。

## 非目标

- 不读取、修改或解析 Codex 配置文件内容。
- 不同步工作区数据，不做备份恢复系统。
- 不在 Linux/Windows 上模拟 Codex App 启停。
- 不删除任何工作区目录，切换时只替换当前工作区链接。

## 代码结构

```text
src/codex_workspaces/
  cli.py         命令分发和进程入口
  config.py      环境变量、默认路径、语言检测
  core.py        工作区目录、链接切换、账号绑定和安装器逻辑
  store.py       统一目录、元数据、账号快照和锁
  stats.py       只读 Codex SQLite 的本地 token 统计
  platforms.py   平台差异：App 控制、Terminal 转交、目录链接
  errors.py      可预期命令错误
```

`macos/codex-workspaces` Bash 脚本继续保留，供 macOS shell 用户使用。根目录保留为 Python 项目入口，Python 包通过 `pyproject.toml` 暴露同名 console script。

## 数据模型

默认布局：

```text
active_link:    ~/.codex
root_dir:       ~/.codex-workspaces
workspace_dir:  ~/.codex-workspaces/workspaces/<name>
accounts_dir:   ~/.codex-workspaces/accounts
```

工作区名规则：

- 允许字母、数字、点、下划线、连字符。
- 必须以字母或数字开头，最长 64 个字符。
- 不允许空字符串、`.`、`..`、路径分隔符、`~` 和路径穿越。
- 输入可以是 `work`、`.codex-work` 或路径形式，内部会归一化成工作区名。

## 平台策略

macOS：

- `switch` 默认执行 stop -> link switch -> start。
- `stop/start/restart` 使用 `pgrep`、`osascript`、`open`、`killall`。
- 检测到 Codex 内置 Terminal 时，`switch/stop/restart` 转交给 Terminal.app。
- `start` 和迁移命令不转交，要求用户在外部 Terminal 执行。

Linux：

- 工作区切换使用目录软链接。
- `switch` 默认跳过 App stop/start，并输出提示。
- 直接执行 `stop/start/restart` 会报“仅 macOS 支持”。

Windows：

- 优先创建目录 symlink。
- 如果 symlink 权限不可用，回退到 `cmd /c mklink /J` 创建 junction。
- 删除当前链接时只删除被识别为 symlink/junction 的路径，拒绝删除真实目录。
- App 控制命令不支持。

## 安全设计

- 如果 `~/.codex` 存在且不是链接，`switch` 拒绝执行，避免覆盖真实目录。
- 旧 `~/.codex-<name>` 工作区迁移和旧 `~/.codex-accounts` 导入已支持。
- `accounts add <account> --login` 使用临时 `login-<account>` 工作区登录新账号，保存账号快照后恢复原工作区。
- 切换 workspace 前会保存当前 live `auth.json` 到 `active_account_id` 对应账号快照。
- `accounts use` 只修改当前 workspace 的 `active_account_id`，不修改 `default_account_id`。
- 进入 workspace 时按策略恢复账号：`workspace-default` 恢复默认账号，`last-active` 恢复该 workspace 上次活跃账号，`keep-current` 尽量沿用刚才正在使用的账号。
- `migrate --dry-run` 只打印计划，不创建目录、不写元数据。
- `migrate` 会先备份当前 `~/.codex`、旧 workspace 和旧账号目录，再复制到统一目录；旧目录不会被自动删除。
- 如果当前 `~/.codex` 是真实目录，批量 `migrate` 会拒绝覆盖；应使用 `init <name> --migrate-current` 显式迁移当前目录。
- `stats` 只以 read-only SQLite URI 读取 `state_*.sqlite`，不写入 Codex 数据库。
- 不实现 `quota`/`refresh` 这类依赖私有接口或私有行为的功能。
- macOS 上迁移前必须确认 Codex App 未运行。
- Codex 内置 Terminal 中的危险命令要么转交外部 Terminal，要么拒绝。
- 任何切换都只替换 active link，不删除 workspace 目录。

## 配置

工作区相关环境变量：

- 变量：`CODEX_WORKSPACES_ROOT`、`CODEX_WORKSPACES_LINK`、`CODEX_WORKSPACES_WORKSPACES_DIR`、`CODEX_WORKSPACES_ACCOUNTS_DIR`、`CODEX_WORKSPACES_RESTORE_POLICY`、`CODEX_WORKSPACES_LANG`。
- `CODEX_WORKSPACES_RESTORE_POLICY` 可选 `workspace-default`、`last-active`、`keep-current`；无效值回退到 `workspace-default`。

迁移相关命令：

- `codex-workspaces migrate --dry-run`
- `codex-workspaces migrate [--from-prefix ~/.codex-] [--from-accounts ~/.codex-accounts]`
- `codex-workspaces init <workspace> --migrate-current`
- `codex-workspaces info <workspace>`
- `codex-workspaces accounts add <account> --login`
- `codex-workspaces accounts login-temp <account>`
- `codex-workspaces accounts cleanup-login-temp`
- `codex-workspaces accounts import-workspaces`
- `codex-workspaces accounts import-legacy <legacy_accounts_dir>`

Python CLI 和 Bash 脚本可以共存。通过 PyPI/pipx 安装时，`codex-workspaces` 命令来自 Python 包；直接执行仓库里的 `macos/codex-workspaces` 时，使用的是 macOS Bash 脚本。

## 发布设计

- `pyproject.toml` 使用 setuptools 构建 wheel 和 sdist。
- console script：`codex-workspaces = codex_workspaces.cli:main`。
- sdist 通过 `MANIFEST.in` 包含 README、CHANGELOG、docs 和 tests；`macos/` 下的原 Bash 脚本保留在仓库中，不随 Python 包发布。
- GitHub CI 负责多平台测试和包检查。
- `v*` tag 触发 TestPyPI Trusted Publishing，`release/v*` 分支触发 PyPI Trusted Publishing。
