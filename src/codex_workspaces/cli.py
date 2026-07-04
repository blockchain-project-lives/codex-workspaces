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
    if command == "info":
        if len(args) != 1:
            manager.fail(
                "用法: codex-workspaces info <工作区名>",
                "Usage: codex-workspaces info <workspace>",
            )
        manager.workspace_info(args[0])
        return 0
    if command in {"doctor", "diagnose"}:
        manager.doctor()
        return 0
    if command == "accounts":
        return run_accounts(args, manager)
    if command == "stats":
        name = None
        days = 7
        index = 0
        while index < len(args):
            arg = args[index]
            if arg in {"-h", "--help"}:
                manager.info(usage(manager.config.lang))
                return 0
            if arg == "--days":
                index += 1
                if index >= len(args):
                    manager.fail("缺少 --days 数值", "Missing value for --days")
                days = _parse_days(args[index], manager)
            elif arg.startswith("--days="):
                days = _parse_days(arg.split("=", 1)[1], manager)
            elif arg.startswith("-"):
                manager.fail(f"未知参数: {arg}", f"Unknown option: {arg}")
            elif name is None:
                name = arg
            else:
                manager.fail(f"未知参数: {arg}", f"Unknown option: {arg}")
            index += 1
        manager.show_stats(name, days)
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
    if command in {"init", "create", "new"}:
        if not args:
            manager.init_workspace("", [])
        else:
            manager.init_workspace(args[0], args[1:])
        return 0
    if command in {"rename", "mv"}:
        if len(args) != 2:
            manager.fail(
                "用法: codex-workspaces rename <旧工作区名> <新工作区名>",
                "Usage: codex-workspaces rename <old-workspace> <new-workspace>",
            )
        manager.rename_workspace(args[0], args[1])
        return 0
    if command in {"delete", "remove", "rm"}:
        if not args:
            manager.fail(
                "用法: codex-workspaces delete <工作区名> --force",
                "Usage: codex-workspaces delete <workspace> --force",
            )
        manager.delete_workspace(args[0], args[1:])
        return 0
    if command == "note":
        if not args:
            manager.fail(
                "用法: codex-workspaces note <工作区名> [备注文本|--clear]",
                "Usage: codex-workspaces note <workspace> [note text|--clear]",
            )
        manager.note_workspace(args[0], args[1:])
        return 0
    if command == "install":
        if len(args) > 1:
            manager.fail(f"未知参数: {args[1]}", f"Unknown option: {args[1]}")
        manager.install_self(args[0] if args else None)
        return 0
    if command == "migrate":
        dry_run = False
        from_prefix = None
        from_accounts = None
        index = 0
        while index < len(args):
            arg = args[index]
            if arg == "--dry-run":
                dry_run = True
            elif arg == "--from-prefix":
                index += 1
                if index >= len(args):
                    manager.fail("缺少 --from-prefix 路径", "Missing value for --from-prefix")
                from_prefix = args[index]
            elif arg.startswith("--from-prefix="):
                from_prefix = arg.split("=", 1)[1]
            elif arg == "--from-accounts":
                index += 1
                if index >= len(args):
                    manager.fail("缺少 --from-accounts 路径", "Missing value for --from-accounts")
                from_accounts = args[index]
            elif arg.startswith("--from-accounts="):
                from_accounts = arg.split("=", 1)[1]
            else:
                manager.fail(f"未知参数: {arg}", f"Unknown option: {arg}")
            index += 1
        manager.migrate(dry_run=dry_run, from_prefix=from_prefix, from_accounts=from_accounts)
        return 0

    try:
        if workspace_dir(manager.config, command).is_dir():
            manager.switch_workspace(command, args, argv)
            return 0
    except CodexWorkspacesError:
        pass

    manager.fail(f"未知命令或工作区不存在: {command}", f"Unknown command or workspace does not exist: {command}")
    return 1


def run_accounts(args: Sequence[str], manager: WorkspaceManager) -> int:
    command = args[0] if args else "list"
    rest = list(args[1:])
    if command in {"list", "ls"}:
        if rest:
            manager.fail(f"未知参数: {rest[0]}", f"Unknown option: {rest[0]}")
        manager.accounts_list()
        return 0
    if command in {"current", "whoami"}:
        if rest:
            manager.fail(f"未知参数: {rest[0]}", f"Unknown option: {rest[0]}")
        manager.accounts_current()
        return 0
    if command == "info":
        if len(rest) != 1:
            manager.fail(
                "用法: codex-workspaces accounts info <账号>",
                "Usage: codex-workspaces accounts info <account>",
            )
        manager.accounts_info(rest[0])
        return 0
    if command == "init":
        if len(rest) != 1:
            manager.fail(
                "用法: codex-workspaces accounts init <账号>",
                "Usage: codex-workspaces accounts init <account>",
            )
        manager.accounts_init(rest[0])
        return 0
    if command == "save":
        if len(rest) != 1:
            manager.fail(
                "用法: codex-workspaces accounts save <账号>",
                "Usage: codex-workspaces accounts save <account>",
            )
        manager.accounts_save(rest[0])
        return 0
    if command == "add":
        if not rest:
            manager.fail(
                "用法: codex-workspaces accounts add <账号> --login",
                "Usage: codex-workspaces accounts add <account> --login",
            )
        manager.accounts_add(rest[0], rest[1:])
        return 0
    if command == "login-temp":
        if not rest:
            manager.fail(
                "用法: codex-workspaces accounts login-temp <账号>",
                "Usage: codex-workspaces accounts login-temp <account>",
            )
        manager.accounts_add(rest[0], ["--login", *rest[1:]])
        return 0
    if command == "cleanup-login-temp":
        manager.accounts_cleanup_login_temp(rest)
        return 0
    if command == "use":
        if len(rest) != 1:
            manager.fail(
                "用法: codex-workspaces accounts use <账号>",
                "Usage: codex-workspaces accounts use <account>",
            )
        manager.accounts_use(rest[0])
        return 0
    if command == "restore-default":
        if len(rest) > 1:
            manager.fail(
                "用法: codex-workspaces accounts restore-default [工作区]",
                "Usage: codex-workspaces accounts restore-default [workspace]",
            )
        manager.accounts_restore_default(rest[0] if rest else None)
        return 0
    if command == "set-default":
        activate = False
        positional = []
        for arg in rest:
            if arg == "--activate":
                activate = True
            else:
                positional.append(arg)
        if len(positional) != 2:
            manager.fail(
                "用法: codex-workspaces accounts set-default <工作区> <账号> [--activate]",
                "Usage: codex-workspaces accounts set-default <workspace> <account> [--activate]",
            )
        manager.accounts_set_default(positional[0], positional[1], activate)
        return 0
    if command in {"rename", "mv"}:
        if len(rest) != 2:
            manager.fail(
                "用法: codex-workspaces accounts rename <旧账号> <新账号>",
                "Usage: codex-workspaces accounts rename <old-account> <new-account>",
            )
        manager.accounts_rename(rest[0], rest[1])
        return 0
    if command in {"delete", "remove", "rm"}:
        if not rest:
            manager.fail(
                "用法: codex-workspaces accounts delete <账号> --force",
                "Usage: codex-workspaces accounts delete <account> --force",
            )
        manager.accounts_delete(rest[0], rest[1:])
        return 0
    if command == "note":
        if not rest:
            manager.fail(
                "用法: codex-workspaces accounts note <账号> [备注文本|--clear]",
                "Usage: codex-workspaces accounts note <account> [note text|--clear]",
            )
        manager.accounts_note(rest[0], rest[1:])
        return 0
    if command == "import-workspaces":
        if rest:
            manager.fail(f"未知参数: {rest[0]}", f"Unknown option: {rest[0]}")
        manager.accounts_import_workspaces()
        return 0
    if command == "import-legacy":
        if len(rest) != 1:
            manager.fail(
                "用法: codex-workspaces accounts import-legacy <旧账号目录>",
                "Usage: codex-workspaces accounts import-legacy <legacy-accounts-dir>",
            )
        manager.accounts_import_legacy(rest[0])
        return 0
    manager.fail(f"未知 accounts 命令: {command}", f"Unknown accounts command: {command}")
    return 1


def _parse_days(value: str, manager: WorkspaceManager) -> int:
    try:
        days = int(value)
    except ValueError:
        manager.fail("--days 必须是正整数", "--days must be a positive integer")
    if days < 1 or days > 90:
        manager.fail("--days 必须在 1 到 90 之间", "--days must be between 1 and 90")
    return days


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
