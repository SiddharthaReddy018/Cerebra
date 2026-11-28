"""QuotaLedger that survives restarts and resets at midnight Pacific (ADR-B §1, §6, FR-5.7).

YouTube's real quota is per Google Cloud project per Pacific day, not per server process.
Without persistence, restarting the MCP server would reset our counters to zero while the
real quota is still spent, so the ledger could approve work YouTube will then refuse.
"""

from __future__ import annotations

import datetime

from cerebra.domain.quota import QuotaLedger
from cerebra.ports.interfaces import Clock, QuotaStore


def pacific_day(ts: float) -> str:
    """YYYY-MM-DD of ``ts`` in America/Los_Angeles (UTC-8 fallback if tzdata is missing)."""
    try:
        from zoneinfo import ZoneInfo

        tz: datetime.tzinfo = ZoneInfo("America/Los_Angeles")
    except Exception:
        tz = datetime.timezone(datetime.timedelta(hours=-8))
    return datetime.datetime.fromtimestamp(ts, tz=tz).date().isoformat()


class PersistentQuotaLedger(QuotaLedger):
    def __init__(
        self,
        store: QuotaStore,
        clock: Clock,
        general_budget: int,
        search_budget: int,
    ) -> None:
        super().__init__(general_budget=general_budget, search_budget=search_budget)
        self._store = store
        self._clock = clock
        self._day = pacific_day(clock.utcnow_timestamp())
        self._restore()

    # ── persistence ──────────────────────────────────────────────────────────

    def _restore(self) -> None:
        state = self._store.load_state()
        if state.get("day") != self._day:
            return
        with self._lock:
            self._general.used = int(state.get("general_used", 0))  # type: ignore[call-overload]
            self._search.used = int(state.get("search_used", 0))  # type: ignore[call-overload]
            self._write_used = int(state.get("write_used", 0))  # type: ignore[call-overload]

    def _save(self) -> None:
        with self._lock:
            state = {
                "day": self._day,
                "general_used": self._general.used,
                "search_used": self._search.used,
                "write_used": self._write_used,
            }
        self._store.save_state(state)

    def _roll_day(self) -> None:
        today = pacific_day(self._clock.utcnow_timestamp())
        if today != self._day:
            with self._lock:
                self._day = today
                self._general.used = 0
                self._search.used = 0
                self._write_used = 0
            self._save()

    # ── overrides ────────────────────────────────────────────────────────────

    def reserve(self, general: int = 0, search: int = 0) -> bool:
        self._roll_day()
        return super().reserve(general=general, search=search)

    def can_afford(self, general: int = 0, search: int = 0) -> bool:
        self._roll_day()
        return super().can_afford(general=general, search=search)

    def consume(self, method: str, count: int = 1) -> None:
        self._roll_day()
        super().consume(method, count)
        self._save()

    def status(self) -> dict[str, object]:
        self._roll_day()
        out = super().status()
        out["day_pacific"] = self._day
        return out
