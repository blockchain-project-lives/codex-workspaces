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
  core.py        工作区目录、链接切换、迁移和安装器逻辑
  stats.py       只读 Codex SQLite 的本地 token 统计
  platforms.py   平台差异：App 控制、Terminal 转交、目录链接
  errors.py      可预期命令错误
```

`macos/codex-workspaces` Bash 脚本继续保留，供 macOS shell 用户使用。根目录保留为 Python 项目入口，Python 包通过 `pyproject.toml` 暴露同名 console script。

## 数据模型

默认布局：

```text
active_link:    ~/.codex
workspace_prefix: ~/.codex-
workspace_dir:    ~/.codex-<name>
```

工作区名规则：

- 允许字母、数字、点、下划线、连字符。
- 不允许空字符串、`.`、`..`。
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
- `init --migrate-current` 只迁移真实目录，不迁移已有链接。
- `stats` 只以 read-only SQLite URI 读取 `state_*.sqlite`，不写入 Codex 数据库。
- 不实现 `quota`/`refresh` 这类依赖私有接口或私有行为的功能。
- macOS 上迁移前必须确认 Codex App 未运行。
- Codex 内置 Terminal 中的危险命令要么转交外部 Terminal，要么拒绝。
- 任何切换都只替换 active link，不删除 `~/.codex-<name>` 工作区目录。

## 配置

工作区相关环境变量：

- 变量：`CODEX_WORKSPACES_LINK`、`CODEX_WORKSPACES_PREFIX`、`CODEX_WORKSPACES_LANG`。

Python CLI 和 Bash 脚本可以共存。通过 PyPI/pipx 安装时，`codex-workspaces` 命令来自 Python 包；直接执行仓库里的 `macos/codex-workspaces` 时，使用的是 macOS Bash 脚本。

## 发布设计

- `pyproject.toml` 使用 setuptools 构建 wheel 和 sdist。
- console script：`codex-workspaces = codex_workspaces.cli:main`。
- sdist 通过 `MANIFEST.in` 包含 README、CHANGELOG、docs 和 tests；`macos/` 下的原 Bash 脚本保留在仓库中，不随 Python 包发布。
- GitHub CI 负责多平台测试和包检查。
- `v*` tag 触发 TestPyPI Trusted Publishing，`release/v*` 分支触发 PyPI Trusted Publishing。
