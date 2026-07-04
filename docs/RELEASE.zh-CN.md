# 发布流程

## 版本号

版本号目前维护在两个位置：

- `pyproject.toml` 的 `project.version`
- `src/codex_workspaces/__init__.py` 的 `__version__`

发布前两处必须一致。

## 发布前检查

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest
python3 -m build
python3 -m twine check dist/*
```

确认 `dist/` 中有：

```text
codex_workspaces-<version>-py3-none-any.whl
codex_workspaces-<version>.tar.gz
```

`dist/` 是构建产物，不提交到 Git。

## PyPI Trusted Publishing 配置

本项目使用 PyPI/TestPyPI 的 Trusted Publishing，不需要在 GitHub 保存 `PYPI_TOKEN`。官方要求发布 job 具备 `id-token: write` 权限，并且 workflow、repository、environment 名称必须和 PyPI/TestPyPI 上登记的信息一致。

在 PyPI 项目中添加 GitHub Trusted Publisher：

```text
Owner: blockchain-project-lives
Repository: codex-workspaces
Workflow name: publish.yml
Environment name: pypi
```

如果 PyPI 项目尚未创建，第一次发布可以先手动创建项目名，或通过受信发布流程创建。若 `codex-workspaces` 名称已被占用，需要调整 `pyproject.toml` 中的 `project.name`。

## TestPyPI Trusted Publishing 配置

TestPyPI 是独立站点，需要单独注册账号、单独配置 Trusted Publisher。配置如下：

```text
Owner: blockchain-project-lives
Repository: codex-workspaces
Workflow name: publish-testpypi.yml
Environment name: testpypi
Repository URL: https://test.pypi.org/legacy/
```

TestPyPI 的包版本同样不能覆盖。`publish-testpypi.yml` 设置了 `skip-existing: true`，但如果你想完整测试新上传，仍建议先提升 patch/dev 版本。

## GitHub Environment

仓库需要创建两个 Environment：

```text
pypi
testpypi
```

建议配置：

- `pypi`：启用 Required reviewers，并限制只允许 `release/v*` 分支部署。
- `testpypi`：可以不设 reviewer，方便试发；如果要限制部署来源，建议允许 `v*` tag。
- 两个 Environment 都不需要配置 secrets。

工作流权限：

```yaml
permissions:
  contents: read
  id-token: write
```

`id-token: write` 用于 PyPI OIDC，不需要在仓库里保存 PyPI token。

当前 workflow 中只有发布 job 设置了 `id-token: write`，CI 和 build 检查 job 没有发布权限。

## 发布步骤

1. 更新版本号和 `CHANGELOG.md`。
2. 本地执行测试和构建检查。
3. 合并到 `main`。
4. 创建 Git tag，例如 `v0.2.1`，触发 `Publish to TestPyPI`。
5. 确认 TestPyPI 上传和安装正常。
6. 从同一个提交创建并推送正式发布分支，例如 `release/v0.2.1`，触发 `Publish to PyPI`。
7. 如果 `pypi` Environment 配置了 Required reviewers，在 GitHub Actions 里批准部署。
8. 在 PyPI 页面确认 wheel、sdist 和 README 渲染正常。

## 手动触发

`publish-testpypi.yml` 支持 `v*` tag 和手动触发，用于试发。

`publish.yml` 支持 `release/v*` 分支和 `workflow_dispatch` 触发。若你的 `pypi` Environment 只允许 `release/v*`，请从 `release/v<版本号>` 分支发布，不要直接用 tag 发布正式 PyPI。

## 回滚策略

PyPI 不能覆盖同版本文件。如果发布错误：

- 不删除用户可能已经安装的版本，除非包含敏感信息。
- 修复后提升 patch 版本重新发布。
- 在 `CHANGELOG.md` 说明问题版本和修复版本。
