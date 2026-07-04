# 测试设计

## 测试目标

测试覆盖三类风险：

- 文件系统行为：初始化统一目录工作区、切换链接、拒绝覆盖真实目录。
- CLI 行为：命令别名、工作区名快捷切换、错误路径、帮助输出。
- 管理行为：诊断、列表元信息、工作区详情、重命名、删除保护、备注读写和账号绑定。
- 账号行为：账号快照保存、auth 元信息 best-effort 解析、列表/详情增强、备份导出导入、备注、重命名、删除保护、临时切换、login-temp 新增账号、默认账号恢复、默认账号设置。
- 迁移行为：旧 `~/.codex-<name>` 工作区迁移、旧 `~/.codex-accounts` 导入、dry-run 不落盘、迁移前备份。
- 统计行为：只读 `state_*.sqlite`，汇总 input/output/total token、模型、最近会话、每日、workspace、account，并覆盖 JSON/Markdown 输出。
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
CODEX_WORKSPACES_ROOT="$tmp_home/.codex-workspaces" \
codex-workspaces init personal

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_ROOT="$tmp_home/.codex-workspaces" \
codex-workspaces switch personal --no-stop --no-start

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_ROOT="$tmp_home/.codex-workspaces" \
codex-workspaces current

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_ROOT="$tmp_home/.codex-workspaces" \
codex-workspaces info personal

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_ROOT="$tmp_home/.codex-workspaces" \
codex-workspaces doctor

# 如果该工作区已有 Codex state_*.sqlite，可验证 stats:
# CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
# CODEX_WORKSPACES_ROOT="$tmp_home/.codex-workspaces" \
# codex-workspaces stats personal --days 14
# codex-workspaces stats summary --days 30
# codex-workspaces stats daily --format markdown
# codex-workspaces stats models --format json
# codex-workspaces stats workspaces
# codex-workspaces stats accounts

printf '{"account":"personal"}\n' > "$tmp_home/.codex-workspaces/workspaces/personal/auth.json"

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_ROOT="$tmp_home/.codex-workspaces" \
codex-workspaces accounts save personal

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_ROOT="$tmp_home/.codex-workspaces" \
codex-workspaces accounts set-default personal acct_personal --activate

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_ROOT="$tmp_home/.codex-workspaces" \
codex-workspaces accounts list

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_ROOT="$tmp_home/.codex-workspaces" \
codex-workspaces accounts info personal

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_ROOT="$tmp_home/.codex-workspaces" \
codex-workspaces accounts refresh-meta personal

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_ROOT="$tmp_home/.codex-workspaces" \
codex-workspaces accounts export "$tmp_home/accounts-meta.tar.gz" --all

# 包含 auth.json 的备份包含凭据，必须安全保存，不能提交到 git。
CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_ROOT="$tmp_home/.codex-workspaces" \
codex-workspaces accounts export "$tmp_home/accounts-with-auth.tar.gz" --all --include-auth --yes

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_ROOT="$tmp_home/.codex-workspaces" \
codex-workspaces accounts import "$tmp_home/accounts-with-auth.tar.gz" --dry-run

# 新账号登录流程会切到临时 login-research 工作区，登录完成后恢复原工作区。
# CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
# CODEX_WORKSPACES_ROOT="$tmp_home/.codex-workspaces" \
# codex-workspaces accounts add research --login

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_ROOT="$tmp_home/.codex-workspaces" \
codex-workspaces note personal "primary workspace"

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_ROOT="$tmp_home/.codex-workspaces" \
codex-workspaces rename personal main

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_ROOT="$tmp_home/.codex-workspaces" \
codex-workspaces init scratch

CODEX_WORKSPACES_LINK="$tmp_home/.codex" \
CODEX_WORKSPACES_ROOT="$tmp_home/.codex-workspaces" \
codex-workspaces delete scratch --force
```

迁移验收可以用临时 HOME 模拟旧布局：

```bash
tmp_home="$(mktemp -d)"
mkdir -p "$tmp_home/.codex-work" "$tmp_home/.codex-accounts/research"
printf '{"account":"work"}\n' > "$tmp_home/.codex-work/auth.json"
printf '{"account":"research"}\n' > "$tmp_home/.codex-accounts/research/auth.json"
ln -s "$tmp_home/.codex-work" "$tmp_home/.codex"

HOME="$tmp_home" codex-workspaces migrate --dry-run
HOME="$tmp_home" codex-workspaces migrate
HOME="$tmp_home" codex-workspaces accounts list
```

预期结果：

- `~/.codex-workspaces/workspaces/work` 存在。
- `~/.codex-workspaces/accounts/acct_work` 和 `acct_research` 存在。
- `~/.codex` 指向新的 `workspaces/work`。
- `~/.codex-work` 和 `~/.codex-accounts` 仍保留。
- `~/.codex-workspaces/backups/<timestamp>/before-migrate/` 下有迁移前备份。
- `migrate` 最后输出迁移报告，包含 migrated/skipped/renamed-conflict/special-file-skipped 摘要。

macOS 上再额外验证：

```bash
codex-workspaces stop
codex-workspaces start
codex-workspaces restart
```

这些命令会真实控制 Codex App，应在确认当前工作已保存后执行。
