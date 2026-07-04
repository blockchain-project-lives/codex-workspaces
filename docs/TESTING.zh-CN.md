# 测试设计

## 测试目标

测试覆盖三类风险：

- 文件系统行为：初始化工作区、迁移工作区、切换链接、拒绝覆盖真实目录。
- CLI 行为：命令别名、工作区名快捷切换、错误路径、帮助输出。
- 管理行为：诊断、列表元信息、重命名、删除保护和备注读写。
- 统计行为：只读 `state_*.sqlite`，汇总 token、模型、最近会话和每日用量。
- 平台行为：macOS App 控制可注入，非 macOS 自动跳过 App 启停，Codex 内置 Terminal 阻止或转交危险操作。

## 测试结构

```text
tests/test_core.py  WorkspaceManager 和工作区规则测试
tests/test_cli.py   CLI 分发测试
```

测试使用 `tmp_path` 创建临时 HOME，不接触真实 `~/.codex`。

`FakePlatform` 继承 `SystemPlatform`，覆盖 App 控制和 Terminal 检测方法，避免测试中真的执行 `open`、`osascript`、`killall` 或关闭 Codex。

## 本地测试命令

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest
python3 -m compileall src tests
python3 -m build
python3 -m twine check dist/*
```

## CI 矩阵

GitHub Actions 在以下环境运行测试：

- `ubuntu-latest`
- `macos-latest`
- `windows-latest`
- Python `3.9`、`3.11`、`3.13`

单独的 package job 会执行：

```bash
python -m build
python -m twine check dist/*
```

## 手动验收建议

在不影响真实工作区的临时目录中测试：

```bash
tmp_home="$(mktemp -d)"
CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_PREFIX="$tmp_home/.codex-" \
codex-workspaces init personal

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_PREFIX="$tmp_home/.codex-" \
codex-workspaces switch personal --no-stop --no-start

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_PREFIX="$tmp_home/.codex-" \
codex-workspaces current

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_PREFIX="$tmp_home/.codex-" \
codex-workspaces doctor

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_PREFIX="$tmp_home/.codex-" \
codex-workspaces stats personal --days 14

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_PREFIX="$tmp_home/.codex-" \
codex-workspaces note personal "primary workspace"

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_PREFIX="$tmp_home/.codex-" \
codex-workspaces rename personal main

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_PREFIX="$tmp_home/.codex-" \
codex-workspaces init scratch

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_PREFIX="$tmp_home/.codex-" \
codex-workspaces delete scratch --force
```

macOS 上再额外验证：

```bash
codex-workspaces stop
codex-workspaces start
codex-workspaces restart
```

这些命令会真实控制 Codex App，应在确认当前工作已保存后执行。
