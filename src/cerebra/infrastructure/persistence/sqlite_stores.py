"""SQLite-backed persistence infrastructure (ADR-E, TBD-3).

Implements: ProposalStore, CacheStore, AuditLog, QuotaStore.
All writes are transactional. No global state.
"""

from __future__ import annotations

import json
import pickle
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, cast

from cerebra.domain.entities import (
    PlaylistItem,
    PlaylistProposal,
    PrivacyStatus,
    ProposalState,
)


class SQLiteDB:
    """Thread-safe SQLite connection factory."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS proposals (
                    proposal_id TEXT PRIMARY KEY,
                    data        TEXT NOT NULL,
                    state       TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    expires_at  TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cache (
                    key        TEXT PRIMARY KEY,
                    value_blob BLOB NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    playlist_id TEXT NOT NULL,
                    event       TEXT NOT NULL DEFAULT 'created'
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    proposal_id TEXT PRIMARY KEY,
                    digest      TEXT NOT NULL,
                    account_id  TEXT NOT NULL,
                    approved_at TEXT NOT NULL,
                    used        INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS quota_state (
                    id    INTEGER PRIMARY KEY CHECK (id = 1),
                    state TEXT NOT NULL
                );
            """)
            # Migration: databases created before Phase 4 lack audit_log.event
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(audit_log)")}
            if "event" not in cols:
                conn.execute(
                    "ALTER TABLE audit_log ADD COLUMN event TEXT NOT NULL DEFAULT 'created'"
                )
            conn.commit()
            conn.close()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(sql, params)
                conn.commit()
                return cur.fetchall()
            finally:
                conn.close()

    def executemany(self, sql: str, params_list: list[tuple[Any, ...]]) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executemany(sql, params_list)
                conn.commit()
            finally:
                conn.close()


class SQLiteProposalStore:
    """ProposalStore backed by SQLite (FR-4.5, ADR-C)."""

    def __init__(self, db: SQLiteDB) -> None:
        self._db = db

    def save(self, proposal: PlaylistProposal) -> None:
        data = json.dumps(self._proposal_to_dict(proposal))
        self._db.execute(
            """INSERT OR REPLACE INTO proposals (proposal_id, data, state, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                proposal.proposal_id,
                data,
                proposal.state.value,
                proposal.created_at,
                proposal.expires_at,
            ),
        )

    def load(self, proposal_id: str) -> PlaylistProposal | None:
        rows = self._db.execute("SELECT data FROM proposals WHERE proposal_id = ?", (proposal_id,))
        if not rows:
            return None
        return self._dict_to_proposal(json.loads(rows[0]["data"]))

    def update_state(self, proposal_id: str, **kwargs: object) -> None:
        proposal = self.load(proposal_id)
        if proposal is None:
            return
        # Apply updates
        d = self._proposal_to_dict(proposal)
        for k, v in kwargs.items():
            if (k == "state" and isinstance(v, ProposalState)) or (
                k == "privacy_status" and isinstance(v, PrivacyStatus)
            ):
                d[k] = v.value
            else:
                d[k] = v
        new_state = d.get("state", proposal.state.value)
        data = json.dumps(d)
        self._db.execute(
            "UPDATE proposals SET data = ?, state = ? WHERE proposal_id = ?",
            (data, new_state, proposal_id),
        )

    def list_active(self) -> list[PlaylistProposal]:
        rows = self._db.execute(
            "SELECT data FROM proposals WHERE state IN ('PROPOSED','CONFIRMED','PARTIAL')"
        )
        return [self._dict_to_proposal(json.loads(r["data"])) for r in rows]

    def _proposal_to_dict(self, p: PlaylistProposal) -> dict[str, object]:
        return {
            "proposal_id": p.proposal_id,
            "topic": p.topic,
            "total_duration_seconds": p.total_duration_seconds,
            "max_video_duration_seconds": p.max_video_duration_seconds,
            "language": p.language,
            "items": [
                {
                    "video_id": i.video_id,
                    "title": i.title,
                    "channel_title": i.channel_title,
                    "duration_seconds": i.duration_seconds,
                }
                for i in p.items
            ],
            "created_at": p.created_at,
            "expires_at": p.expires_at,
            "state": p.state.value,
            "digest": p.digest,
            "privacy_status": p.privacy_status.value,
            "quota_reservation_general": p.quota_reservation_general,
            "quota_reservation_write": p.quota_reservation_write,
            "playlist_id": p.playlist_id,
            "playlist_url": p.playlist_url,
            "failed_item_ids": p.failed_item_ids,
            "item_states": p.item_states,
            "ambiguous": p.ambiguous,
            "account_id": p.account_id,
        }

    def _dict_to_proposal(self, d: dict[str, object]) -> PlaylistProposal:
        items = [
            PlaylistItem(
                video_id=str(i["video_id"]),
                title=str(i["title"]),
                channel_title=str(i["channel_title"]),
                duration_seconds=int(cast(int, i["duration_seconds"])),
            )
            for i in cast(list[dict[str, object]], d.get("items", []))
        ]
        return PlaylistProposal(
            proposal_id=str(d["proposal_id"]),
            topic=str(d["topic"]),
            total_duration_seconds=int(cast(int, d.get("total_duration_seconds", 0))),
            max_video_duration_seconds=cast("int | None", d.get("max_video_duration_seconds")),
            language=cast("str | None", d.get("language")),
            items=items,
            created_at=str(d["created_at"]),
            expires_at=str(d["expires_at"]),
            state=ProposalState(d["state"]),
            digest=str(d["digest"]),
            privacy_status=PrivacyStatus(d.get("privacy_status", "private")),
            quota_reservation_general=int(cast(int, d.get("quota_reservation_general", 0))),
            quota_reservation_write=int(cast(int, d.get("quota_reservation_write", 0))),
            playlist_id=cast("str | None", d.get("playlist_id")),
            playlist_url=cast("str | None", d.get("playlist_url")),
            failed_item_ids=cast(list[str], d.get("failed_item_ids", [])),
            item_states=cast(dict[str, str], d.get("item_states", {})),
            ambiguous=bool(d.get("ambiguous", False)),
            account_id=str(d.get("account_id", "")),
        )


class SQLiteCacheStore:
    """CacheStore backed by SQLite with TTL (FR-5.9, NFR-PV-2)."""

    def __init__(
        self, db: SQLiteDB, max_size: int = 1000, max_ttl_seconds: int | None = None
    ) -> None:
        self._db = db
        self._max_size = max_size
        # NFR-PV-2: configurable retention; no entry outlives it, whatever TTL the caller asks for.
        self._max_ttl = max_ttl_seconds

    def get(self, key: str) -> object | None:
        rows = self._db.execute(
            "SELECT value_blob FROM cache WHERE key = ? AND expires_at > ?",
            (key, time.time()),
        )
        if not rows:
            return None
        return cast(object, pickle.loads(rows[0]["value_blob"]))  # noqa: S301

    def set(self, key: str, value: object, ttl_seconds: int) -> None:
        blob = pickle.dumps(value)
        if self._max_ttl is not None:
            ttl_seconds = min(ttl_seconds, self._max_ttl)
        expires = time.time() + ttl_seconds
        self._db.execute(
            "INSERT OR REPLACE INTO cache (key, value_blob, expires_at) VALUES (?, ?, ?)",
            (key, blob, expires),
        )
        self._evict_if_needed()

    def delete(self, key: str) -> None:
        self._db.execute("DELETE FROM cache WHERE key = ?", (key,))

    def clear_all(self) -> None:
        self._db.execute("DELETE FROM cache")

    def stats(self) -> dict[str, object]:
        rows = self._db.execute(
            "SELECT COUNT(*) as cnt FROM cache WHERE expires_at > ?", (time.time(),)
        )
        return {"active_entries": rows[0]["cnt"] if rows else 0, "max_size": self._max_size}

    def _evict_if_needed(self) -> None:
        # Remove expired entries first
        self._db.execute("DELETE FROM cache WHERE expires_at <= ?", (time.time(),))
        rows = self._db.execute("SELECT COUNT(*) as cnt FROM cache")
        count = rows[0]["cnt"] if rows else 0
        if count > self._max_size:
            # Remove oldest entries
            excess = count - self._max_size
            self._db.execute(
                "DELETE FROM cache WHERE key IN (SELECT key FROM cache ORDER BY expires_at ASC LIMIT ?)",
                (excess,),
            )


class SQLiteAuditLog:
    """AuditLog backed by SQLite (FR-4.8): time + proposal_id + playlist_id only."""

    def __init__(self, db: SQLiteDB) -> None:
        self._db = db

    def log_creation(self, proposal_id: str, playlist_id: str, timestamp: str) -> None:
        self._db.execute(
            "INSERT INTO audit_log (timestamp, proposal_id, playlist_id) VALUES (?, ?, ?)",
            (timestamp, proposal_id, playlist_id),
        )

    def log_event(self, proposal_id: str, playlist_id: str, event: str, timestamp: str) -> None:
        self._db.execute(
            "INSERT INTO audit_log (timestamp, proposal_id, playlist_id, event) "
            "VALUES (?, ?, ?, ?)",
            (timestamp, proposal_id, playlist_id, event),
        )


class SQLiteApprovalStore:
    """ApprovalStore backed by SQLite (ADR-A §3). Written only by ``cerebra-cli approve``."""

    def __init__(self, db: SQLiteDB) -> None:
        self._db = db

    def record(self, proposal_id: str, digest: str, account_id: str, approved_at: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO approvals "
            "(proposal_id, digest, account_id, approved_at, used) VALUES (?, ?, ?, ?, 0)",
            (proposal_id, digest, account_id, approved_at),
        )

    def get(self, proposal_id: str) -> dict[str, str] | None:
        rows = self._db.execute(
            "SELECT proposal_id, digest, account_id, approved_at, used "
            "FROM approvals WHERE proposal_id = ?",
            (proposal_id,),
        )
        if not rows:
            return None
        r = rows[0]
        return {
            "proposal_id": r["proposal_id"],
            "digest": r["digest"],
            "account_id": r["account_id"],
            "approved_at": r["approved_at"],
            "used": str(r["used"]),
        }

    def consume(self, proposal_id: str) -> bool:
        # Single atomic UPDATE: only one caller can flip used 0 -> 1
        with self._db._lock:
            conn = self._db._connect()
            try:
                cur = conn.execute(
                    "UPDATE approvals SET used = 1 WHERE proposal_id = ? AND used = 0",
                    (proposal_id,),
                )
                conn.commit()
                return cur.rowcount == 1
            finally:
                conn.close()


class SQLiteQuotaStore:
    """QuotaStore backed by SQLite (FR-5.7)."""

    def __init__(self, db: SQLiteDB) -> None:
        self._db = db

    def save_state(self, state: dict[str, object]) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO quota_state (id, state) VALUES (1, ?)",
            (json.dumps(state),),
        )

    def load_state(self) -> dict[str, object]:
        rows = self._db.execute("SELECT state FROM quota_state WHERE id = 1")
        if not rows:
            return {}
        return cast(dict[str, object], json.loads(rows[0]["state"]))
