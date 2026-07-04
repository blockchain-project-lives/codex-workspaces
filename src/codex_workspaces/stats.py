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
    tokens: int
    created_at: datetime | None


@dataclass(frozen=True)
class DailyUsage:
    day: date
    tokens: int = 0
    sessions: int = 0


@dataclass
class WorkspaceStats:
    name: str
    source: Path
    sessions: list[SessionUsage] = field(default_factory=list)
    by_model: dict[str, int] = field(default_factory=dict)
    daily: list[DailyUsage] = field(default_factory=list)
    total_tokens: int = 0
    last_7d_tokens: int = 0
    last_7d_sessions: int = 0
    last_30d_tokens: int = 0
    last_30d_sessions: int = 0

    @property
    def total_sessions(self) -> int:
        return len(self.sessions)


def compute_workspace_stats(name: str, root: Path, days: int = 7) -> WorkspaceStats:
    db_path = find_state_db(root)
    rows = read_thread_rows(db_path)
    stats = WorkspaceStats(name=name, source=db_path)

    now = datetime.now(timezone.utc)
    ago_7 = now - timedelta(days=7)
    ago_30 = now - timedelta(days=30)
    days = max(1, days)
    first_day = (now - timedelta(days=days - 1)).astimezone().date()
    daily = {first_day + timedelta(days=offset): [0, 0] for offset in range(days)}

    for row in rows:
        created = parse_timestamp(row["created_at_ms"]) or parse_timestamp(row["created_at"])
        session = SessionUsage(
            title=str(row["title"] or "(untitled)"),
            model=str(row["model"] or "(unknown)"),
            tokens=int(row["tokens_used"] or 0),
            created_at=created,
        )
        stats.sessions.append(session)
        stats.total_tokens += session.tokens
        stats.by_model[session.model] = stats.by_model.get(session.model, 0) + session.tokens

        if created is None:
            continue
        if created >= ago_7:
            stats.last_7d_tokens += session.tokens
            stats.last_7d_sessions += 1
        if created >= ago_30:
            stats.last_30d_tokens += session.tokens
            stats.last_30d_sessions += 1
        local_day = created.astimezone().date()
        if local_day in daily:
            daily[local_day][0] += session.tokens
            daily[local_day][1] += 1

    stats.daily = [DailyUsage(day=day, tokens=values[0], sessions=values[1]) for day, values in daily.items()]
    return stats


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
        required = {"tokens_used"}
        missing = sorted(required - columns)
        if missing:
            raise StatsError(f"{db_path} threads table is missing columns: {', '.join(missing)}")

        title_expr = "title" if "title" in columns else "'(untitled)' AS title"
        model_expr = "model" if "model" in columns else "'(unknown)' AS model"
        created_expr = "created_at" if "created_at" in columns else "NULL AS created_at"
        created_ms_expr = "created_at_ms" if "created_at_ms" in columns else "NULL AS created_at_ms"
        order_expr = "created_at_ms" if "created_at_ms" in columns else "created_at" if "created_at" in columns else "tokens_used"
        return conn.execute(
            f"""
            SELECT {title_expr}, {model_expr}, tokens_used, {created_expr}, {created_ms_expr}
            FROM threads
            WHERE tokens_used > 0
            ORDER BY {order_expr} DESC
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise StatsError(f"could not read {db_path}: {exc}") from exc
    finally:
        conn.close()


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
