from __future__ import annotations

import sys
from typing import Optional, Sequence

from .config import Config
from .core import WorkspaceManager, workspace_dir, usage
from .errors import CodexWorkspacesError
from .platforms import SystemPlatform


def _print_error(message: str, lang: str) -> None:
    prefix = "错误" if lang == "zh" else "Error"
    print(f"{prefix}: {message}", file=sys.stderr)


def run(argv: Sequence[str], manager: WorkspaceManager) -> int:
    command = argv[0] if argv else "help"
    args = list(argv[1:])

    if command in {"help", "-h", "--help"}:
        manager.info(usage(manager.config.lang))
        return 0
    if command in {"list", "ls"}:
        manager.list_workspaces()
        return 0
    if command in {"current", "whoami"}:
        manager.show_current()
        return 0
    if command in {"doctor", "diagnose"}:
        manager.doctor()
        return 0
    if command in {"use", "switch", "sw"}:
        if not args:
            manager.fail(
                "缺少工作区名，例如: codex-workspaces use work",
                "Missing workspace name, for example: codex-workspaces use work",
            )
        manager.switch_workspace(args[0], args[1:], argv)
        return 0
    if command in {"stop", "quit", "close"}:
        force = False
        for arg in args:
            if arg in {"--force", "-f"}:
                force = True
            else:
                manager.fail(f"未知参数: {arg}", f"Unknown option: {arg}")
        manager.stop_codex(force, argv)
        return 0
    if command in {"start", "open"}:
        if args:
            manager.fail("start 不需要参数", "start does not take arguments")
        manager.start_codex()
        return 0
    if command in {"restart", "reopen"}:
        force = False
        for arg in args:
            if arg in {"--force", "-f"}:
                force = True
            else:
                manager.fail(f"未知参数: {arg}", f"Unknown option: {arg}")
        manager.restart_codex(force, argv)
        return 0
    if command in {"create", "new"}:
        if not args:
            manager.create_workspace("", [])
        else:
            manager.create_workspace(args[0], args[1:])
        return 0
    if command == "install":
        if len(args) > 1:
            manager.fail(f"未知参数: {args[1]}", f"Unknown option: {args[1]}")
        manager.install_self(args[0] if args else None)
        return 0

    try:
        if workspace_dir(manager.config, command).is_dir():
            manager.switch_workspace(command, args, argv)
            return 0
    except CodexWorkspacesError:
        pass

    manager.fail(f"未知命令或工作区不存在: {command}", f"Unknown command or workspace does not exist: {command}")
    return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    platform_service = SystemPlatform()
    config = Config.from_env(apple_language=platform_service.apple_language())
    manager = WorkspaceManager(config, platform_service)
    try:
        return run(list(sys.argv[1:] if argv is None else argv), manager)
    except CodexWorkspacesError as exc:
        _print_error(exc.message, config.lang)
        if exc.message.startswith("Unknown command") or exc.message.startswith("未知命令"):
            print(file=sys.stderr)
            print(usage(config.lang), file=sys.stderr)
        return exc.exit_code
