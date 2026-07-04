from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence
from urllib.parse import quote


class StatsError(Exception):
    pass


@dataclass(frozen=True)
class SessionUsage:
    title: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    created_at: datetime | None


@dataclass(frozen=True)
class DailyUsage:
    day: date
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    sessions: int = 0


@dataclass(frozen=True)
class ModelUsage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    sessions: int = 0


@dataclass
class WorkspaceStats:
    name: str
    source: Path
    account_id: str = "unknown"
    sessions: list[SessionUsage] = field(default_factory=list)
    by_model: dict[str, int] = field(default_factory=dict)
    models: list[ModelUsage] = field(default_factory=list)
    daily: list[DailyUsage] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    last_7d_tokens: int = 0
    last_7d_sessions: int = 0
    last_30d_tokens: int = 0
    last_30d_sessions: int = 0

    @property
    def total_sessions(self) -> int:
        return len(self.sessions)


@dataclass
class StatsBundle:
    period_from: date
    period_to: date
    workspaces: list[WorkspaceStats] = field(default_factory=list)
    daily: list[DailyUsage] = field(default_factory=list)
    models: list[ModelUsage] = field(default_factory=list)
    workspace_rows: list[ModelUsage] = field(default_factory=list)
    account_rows: list[ModelUsage] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    total_sessions: int = 0


def compute_workspace_stats(
    name: str,
    root: Path,
    days: int = 7,
    *,
    start_day: date | None = None,
    end_day: date | None = None,
    account_id: str = "unknown",
) -> WorkspaceStats:
    db_path = find_state_db(root)
    rows = read_thread_rows(db_path)
    stats = WorkspaceStats(name=name, source=db_path, account_id=account_id or "unknown")

    now = datetime.now(timezone.utc)
    ago_7 = now - timedelta(days=7)
    ago_30 = now - timedelta(days=30)
    if start_day is None or end_day is None:
        days = max(1, days)
        end_day = now.astimezone().date() if end_day is None else end_day
        start_day = end_day - timedelta(days=days - 1) if start_day is None else start_day
    if start_day > end_day:
        raise StatsError("from date must be before or equal to to date")
    daily = {start_day + timedelta(days=offset): [0, 0, 0, 0] for offset in range((end_day - start_day).days + 1)}
    models: dict[str, list[int]] = {}

    for row in rows:
        created = parse_timestamp(row["created_at_ms"]) or parse_timestamp(row["created_at"])
        input_tokens, output_tokens, total_tokens = row_tokens(row)
        if created is not None:
            local_day = created.astimezone().date()
            if local_day < start_day or local_day > end_day:
                continue
        session = SessionUsage(
            title=str(row["title"] or "(untitled)"),
            model=str(row["model"] or "(unknown)"),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            created_at=created,
        )
        stats.sessions.append(session)
        stats.input_tokens += input_tokens
        stats.output_tokens += output_tokens
        stats.total_tokens += total_tokens
        stats.by_model[session.model] = stats.by_model.get(session.model, 0) + total_tokens
        model_values = models.setdefault(session.model, [0, 0, 0, 0])
        model_values[0] += input_tokens
        model_values[1] += output_tokens
        model_values[2] += total_tokens
        model_values[3] += 1

        if created is None:
            continue
        if created >= ago_7:
            stats.last_7d_tokens += total_tokens
            stats.last_7d_sessions += 1
        if created >= ago_30:
            stats.last_30d_tokens += total_tokens
            stats.last_30d_sessions += 1
        local_day = created.astimezone().date()
        if local_day in daily:
            daily[local_day][0] += input_tokens
            daily[local_day][1] += output_tokens
            daily[local_day][2] += total_tokens
            daily[local_day][3] += 1

    stats.daily = [DailyUsage(day=day, input_tokens=values[0], output_tokens=values[1], total_tokens=values[2], sessions=values[3]) for day, values in daily.items()]
    stats.models = [
        ModelUsage(model=model, input_tokens=values[0], output_tokens=values[1], total_tokens=values[2], sessions=values[3])
        for model, values in sorted(models.items(), key=lambda item: (-item[1][2], item[0]))
    ]
    return stats


def combine_workspace_stats(workspaces: list[WorkspaceStats], period_from: date, period_to: date) -> StatsBundle:
    bundle = StatsBundle(period_from=period_from, period_to=period_to, workspaces=workspaces)
    daily: dict[date, list[int]] = {period_from + timedelta(days=i): [0, 0, 0, 0] for i in range((period_to - period_from).days + 1)}
    models: dict[str, list[int]] = {}
    workspace_rows: list[ModelUsage] = []
    accounts: dict[str, list[int]] = {}

    for workspace in workspaces:
        bundle.input_tokens += workspace.input_tokens
        bundle.output_tokens += workspace.output_tokens
        bundle.total_tokens += workspace.total_tokens
        bundle.total_sessions += workspace.total_sessions
        workspace_rows.append(ModelUsage(workspace.name, workspace.input_tokens, workspace.output_tokens, workspace.total_tokens, workspace.total_sessions))
        account_values = accounts.setdefault(workspace.account_id or "unknown", [0, 0, 0, 0])
        account_values[0] += workspace.input_tokens
        account_values[1] += workspace.output_tokens
        account_values[2] += workspace.total_tokens
        account_values[3] += workspace.total_sessions
        for entry in workspace.daily:
            values = daily.setdefault(entry.day, [0, 0, 0, 0])
            values[0] += entry.input_tokens
            values[1] += entry.output_tokens
            values[2] += entry.total_tokens
            values[3] += entry.sessions
        for entry in workspace.models:
            values = models.setdefault(entry.model, [0, 0, 0, 0])
            values[0] += entry.input_tokens
            values[1] += entry.output_tokens
            values[2] += entry.total_tokens
            values[3] += entry.sessions

    bundle.daily = [DailyUsage(day=day, input_tokens=values[0], output_tokens=values[1], total_tokens=values[2], sessions=values[3]) for day, values in sorted(daily.items())]
    bundle.models = [
        ModelUsage(model=model, input_tokens=values[0], output_tokens=values[1], total_tokens=values[2], sessions=values[3])
        for model, values in sorted(models.items(), key=lambda item: (-item[1][2], item[0]))
    ]
    bundle.workspace_rows = sorted(workspace_rows, key=lambda row: (-row.total_tokens, row.model))
    bundle.account_rows = [
        ModelUsage(model=account, input_tokens=values[0], output_tokens=values[1], total_tokens=values[2], sessions=values[3])
        for account, values in sorted(accounts.items(), key=lambda item: (-item[1][2], item[0]))
    ]
    return bundle


def find_state_db(root: Path) -> Path:
    candidates = list(root.glob("state_*.sqlite"))
    sqlite_dir = root / "sqlite"
    if sqlite_dir.is_dir():
        candidates.extend(sqlite_dir.glob("state_*.sqlite"))
    candidates = [path for path in candidates if path.is_file()]
    if not candidates:
        raise StatsError(f"no state_*.sqlite found under {root}")
    return max(candidates, key=state_db_sort_key)


def state_db_sort_key(path: Path) -> tuple[int, float, str]:
    version = -1
    prefix = "state_"
    suffix = ".sqlite"
    if path.name.startswith(prefix) and path.name.endswith(suffix):
        raw = path.name[len(prefix) : -len(suffix)]
        if raw.isdigit():
            version = int(raw)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return version, mtime, str(path)


def read_thread_rows(db_path: Path) -> Sequence[sqlite3.Row]:
    uri = "file:" + quote(str(db_path), safe="/:\\") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(threads)").fetchall()}
        if not columns:
            raise StatsError(f"{db_path} does not contain a threads table")
        token_expr = token_column_expr(columns)
        if token_expr is None:
            raise StatsError(f"{db_path} threads table is missing token columns")

        title_expr = "title" if "title" in columns else "'(untitled)' AS title"
        model_expr = "model" if "model" in columns else "'(unknown)' AS model"
        created_expr = "created_at" if "created_at" in columns else "NULL AS created_at"
        created_ms_expr = "created_at_ms" if "created_at_ms" in columns else "NULL AS created_at_ms"
        input_expr = first_existing(columns, ["input_tokens", "tokens_input", "prompt_tokens"], "0") + " AS input_tokens"
        output_expr = first_existing(columns, ["output_tokens", "tokens_output", "completion_tokens"], "0") + " AS output_tokens"
        total_expr = token_expr + " AS total_tokens"
        order_expr = "created_at_ms" if "created_at_ms" in columns else "created_at" if "created_at" in columns else token_expr
        return conn.execute(
            f"""
            SELECT {title_expr}, {model_expr}, {input_expr}, {output_expr}, {total_expr}, {created_expr}, {created_ms_expr}
            FROM threads
            WHERE {token_expr} > 0
            ORDER BY {order_expr} DESC
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise StatsError(f"could not read {db_path}: {exc}") from exc
    finally:
        conn.close()


def token_column_expr(columns: set[str]) -> str | None:
    if "total_tokens" in columns:
        return "total_tokens"
    if "tokens_used" in columns:
        return "tokens_used"
    input_col = first_existing(columns, ["input_tokens", "tokens_input", "prompt_tokens"], "")
    output_col = first_existing(columns, ["output_tokens", "tokens_output", "completion_tokens"], "")
    if input_col or output_col:
        return f"COALESCE({input_col or '0'}, 0) + COALESCE({output_col or '0'}, 0)"
    return None


def first_existing(columns: set[str], names: list[str], fallback: str) -> str:
    for name in names:
        if name in columns:
            return name
    return fallback


def row_tokens(row: sqlite3.Row) -> tuple[int, int, int]:
    input_tokens = int(row["input_tokens"] or 0)
    output_tokens = int(row["output_tokens"] or 0)
    total_tokens = int(row["total_tokens"] or 0)
    if total_tokens and not input_tokens and not output_tokens:
        output_tokens = total_tokens
    if not total_tokens:
        total_tokens = input_tokens + output_tokens
    return input_tokens, output_tokens, total_tokens


def parse_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return from_unix(float(value))
    if isinstance(value, str):
        try:
            return from_unix(float(value))
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def from_unix(value: float) -> datetime:
    if value > 1e10:
        value /= 1000
    return datetime.fromtimestamp(value, tz=timezone.utc)
