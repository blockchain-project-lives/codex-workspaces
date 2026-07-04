from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from .auth_inspector import inspect_auth_file
from .config import Config
from .errors import CodexWorkspacesError

SCHEMA_VERSION = 1
WORKSPACE_META_FILE = ".codex-workspace.json"


@dataclass
class WorkspaceMeta:
    name: str
    path: str
    default_account_id: Optional[str]
    active_account_id: Optional[str]
    restore_default_on_enter: bool
    created_at: str
    updated_at: str
    last_used_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "name": self.name,
            "path": self.path,
            "default_account_id": self.default_account_id,
            "active_account_id": self.active_account_id,
            "restore_default_on_enter": self.restore_default_on_enter,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_used_at": self.last_used_at,
        }

    @classmethod
    def from_dict(cls, data: dict, fallback_name: str, fallback_path: Path) -> "WorkspaceMeta":
        now = iso_now()
        return cls(
            name=str(data.get("name") or fallback_name),
            path=str(data.get("path") or fallback_path),
            default_account_id=data.get("default_account_id"),
            active_account_id=data.get("active_account_id"),
            restore_default_on_enter=bool(data.get("restore_default_on_enter", True)),
            created_at=str(data.get("created_at") or now),
            updated_at=str(data.get("updated_at") or now),
            last_used_at=data.get("last_used_at"),
        )


@dataclass
class AccountMeta:
    id: str
    name: str
    source: str
    bound_workspace: Optional[str]
    email: Optional[str]
    plan: Optional[str]
    account_id: Optional[str]
    user_id: Optional[str]
    organization_id: Optional[str]
    auth_hash: Optional[str]
    created_at: str
    updated_at: str
    last_used_at: Optional[str] = None
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "bound_workspace": self.bound_workspace,
            "email": self.email,
            "plan": self.plan,
            "account_id": self.account_id,
            "user_id": self.user_id,
            "organization_id": self.organization_id,
            "auth_hash": self.auth_hash,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_used_at": self.last_used_at,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict, fallback_id: str) -> "AccountMeta":
        now = iso_now()
        return cls(
            id=str(data.get("id") or fallback_id),
            name=str(data.get("name") or fallback_id.removeprefix("acct_")),
            source=str(data.get("source") or "manual"),
            bound_workspace=data.get("bound_workspace"),
            email=data.get("email"),
            plan=data.get("plan"),
            account_id=data.get("account_id"),
            user_id=data.get("user_id"),
            organization_id=data.get("organization_id"),
            auth_hash=data.get("auth_hash"),
            created_at=str(data.get("created_at") or now),
            updated_at=str(data.get("updated_at") or now),
            last_used_at=data.get("last_used_at"),
            notes=str(data.get("notes") or ""),
        )


class WorkspaceStore:
    def __init__(self, config: Config) -> None:
        self.config = config

    def ensure_layout(self) -> None:
        for directory in (
            self.config.root_dir,
            self.config.workspaces_dir,
            self.config.accounts_dir,
            self.config.backups_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
            chmod_best_effort(directory, 0o700)
        self.ensure_config_json()
        self.ensure_state_json()

    def ensure_config_json(self) -> None:
        path = self.config.root_dir / "config.json"
        if path.exists():
            return
        write_json_atomic(
            path,
            {
                "schema_version": SCHEMA_VERSION,
                "root": str(self.config.root_dir),
                "workspaces_dir": "workspaces",
                "accounts_dir": "accounts",
                "current_link": str(self.config.active_link),
                "default_restore_policy": "workspace-default",
                "backup_before_switch": True,
                "backup_before_migrate": True,
                "lock_file": str(self.config.lock_file),
                "language": "auto",
            },
        )

    def ensure_state_json(self) -> None:
        path = self.config.root_dir / "state.json"
        if not path.exists():
            write_json_atomic(path, {"schema_version": SCHEMA_VERSION, "created_at": iso_now()})

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        self.config.root_dir.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.config.lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise CodexWorkspacesError(
                f"lock acquisition failed: {self.config.lock_file}\n"
                "Hint: make sure no other codex-workspaces command is running."
            ) from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(f"pid={os.getpid()} created_at={iso_now()}\n")
            yield
        finally:
            try:
                self.config.lock_file.unlink()
            except FileNotFoundError:
                pass

    def workspace_meta_path(self, directory: Path) -> Path:
        return directory / WORKSPACE_META_FILE

    def read_workspace_meta(self, directory: Path, name: str) -> WorkspaceMeta:
        data = read_json(self.workspace_meta_path(directory))
        return WorkspaceMeta.from_dict(data, name, directory)

    def write_workspace_meta(self, directory: Path, meta: WorkspaceMeta) -> None:
        write_json_atomic(self.workspace_meta_path(directory), meta.to_dict())

    def ensure_workspace_meta(self, name: str, directory: Path) -> WorkspaceMeta:
        path = self.workspace_meta_path(directory)
        if path.exists():
            return self.read_workspace_meta(directory, name)
        now = iso_now()
        meta = WorkspaceMeta(
            name=name,
            path=str(directory),
            default_account_id=None,
            active_account_id=None,
            restore_default_on_enter=True,
            created_at=now,
            updated_at=now,
        )
        self.write_workspace_meta(directory, meta)
        return meta

    def account_dir(self, account_id: str) -> Path:
        return self.config.accounts_dir / account_id

    def account_auth_path(self, account_id: str) -> Path:
        return self.account_dir(account_id) / "auth.json"

    def account_meta_path(self, account_id: str) -> Path:
        return self.account_dir(account_id) / "meta.json"

    def account_exists(self, account_id: str) -> bool:
        return self.account_auth_path(account_id).is_file() and self.account_meta_path(account_id).is_file()

    def read_account_meta(self, account_id: str) -> AccountMeta:
        data = read_json(self.account_meta_path(account_id))
        return AccountMeta.from_dict(data, account_id)

    def write_account_meta(self, meta: AccountMeta) -> None:
        account_dir = self.account_dir(meta.id)
        account_dir.mkdir(parents=True, exist_ok=True)
        chmod_best_effort(account_dir, 0o700)
        write_json_atomic(self.account_meta_path(meta.id), meta.to_dict(), mode=0o600)

    def list_accounts(self) -> list[AccountMeta]:
        if not self.config.accounts_dir.is_dir():
            return []
        accounts: list[AccountMeta] = []
        for directory in sorted(self.config.accounts_dir.iterdir()):
            if not directory.is_dir():
                continue
            meta_path = directory / "meta.json"
            if meta_path.is_file():
                accounts.append(AccountMeta.from_dict(read_json(meta_path), directory.name))
        return accounts

    def create_account(
        self,
        account_id: str,
        *,
        name: str,
        source: str,
        bound_workspace: Optional[str],
        auth_source: Optional[Path],
        notes: str = "",
    ) -> AccountMeta:
        account_dir = self.account_dir(account_id)
        if account_dir.exists():
            raise CodexWorkspacesError(f"account already exists: {account_id}")
        account_dir.mkdir(parents=True, exist_ok=False)
        chmod_best_effort(account_dir, 0o700)
        auth_hash = None
        if auth_source is not None:
            auth_hash = copy_auth(auth_source, self.account_auth_path(account_id))
            inspection = inspect_auth_file(self.account_auth_path(account_id))
            if inspection.auth_hash:
                auth_hash = inspection.auth_hash
        now = iso_now()
        meta = AccountMeta(
            id=account_id,
            name=name,
            source=source,
            bound_workspace=bound_workspace,
            email=None,
            plan=None,
            account_id=None,
            user_id=None,
            organization_id=None,
            auth_hash=auth_hash,
            created_at=now,
            updated_at=now,
            last_used_at=None,
            notes=notes,
        )
        if auth_source is not None:
            self.apply_auth_inspection(meta, self.account_auth_path(account_id), overwrite=False)
        self.write_account_meta(meta)
        return meta

    def save_auth_to_account(self, account_id: str, source: Path, *, refresh_meta: bool = False) -> None:
        if not source.is_file():
            raise CodexWorkspacesError(
                f"current workspace auth.json not found: {source}\n"
                "Hint: login Codex first, or use `codex-workspaces accounts use <account>`."
            )
        if not self.account_meta_path(account_id).is_file():
            raise CodexWorkspacesError(
                f"account not found: {account_id}\nHint: run `codex-workspaces accounts list`"
            )
        auth_hash = copy_auth(source, self.account_auth_path(account_id))
        meta = self.read_account_meta(account_id)
        meta.auth_hash = auth_hash
        self.apply_auth_inspection(meta, self.account_auth_path(account_id), overwrite=refresh_meta)
        meta.updated_at = iso_now()
        self.write_account_meta(meta)

    def refresh_account_meta(self, account_id: str, *, overwrite: bool = False) -> AccountMeta:
        meta = self.read_account_meta(account_id)
        auth_path = self.account_auth_path(account_id)
        if auth_path.is_file():
            self.apply_auth_inspection(meta, auth_path, overwrite=overwrite)
            meta.updated_at = iso_now()
            self.write_account_meta(meta)
        return meta

    def apply_auth_inspection(self, meta: AccountMeta, auth_path: Path, *, overwrite: bool) -> None:
        inspection = inspect_auth_file(auth_path)
        if inspection.auth_hash and (overwrite or not meta.auth_hash):
            meta.auth_hash = inspection.auth_hash
        for field_name in ("email", "account_id", "user_id", "organization_id", "plan"):
            value = getattr(inspection, field_name)
            if value and (overwrite or not getattr(meta, field_name)):
                setattr(meta, field_name, value)

    def touch_account_used(self, account_id: str) -> None:
        meta = self.read_account_meta(account_id)
        now = iso_now()
        meta.last_used_at = now
        meta.updated_at = now
        self.write_account_meta(meta)


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise CodexWorkspacesError(f"invalid JSON metadata: {path}") from exc


def write_json_atomic(path: Path, data: dict, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{int(time.time() * 1000)}.tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    chmod_best_effort(tmp, mode)
    os.replace(tmp, path)


def chmod_best_effort(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        pass


def auth_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def copy_auth(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    shutil.copy2(source, tmp)
    chmod_best_effort(tmp, 0o600)
    os.replace(tmp, destination)
    return auth_hash(destination)
