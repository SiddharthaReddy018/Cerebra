"""Quota view for use cases when the live adapter meters every call itself (ADR-B).

With the live adapter, each API call reserves → calls → consumes against the shared
QuotaLedger (FR-5.8), so the ledger already holds the *actual* usage. If the use cases also
held worst-case reservations and called ``consume`` for their own estimates, units would be
counted twice and (near the daily limit) the adapter's per-call reservation could be refused
in the middle of a workflow.

``AdapterMeteredQuota`` therefore gives use cases exactly the part of the ledger API they
need for the FR-5.8 *up-front* decision:

  * ``can_afford`` / ``reserve``  → check-only: refuse BEFORE the first API call if the whole
    workflow does not fit. ``reserve`` holds nothing and returns the check result.
  * ``release`` / ``consume``     → no-ops (the adapter does the real accounting).

Everything else (estimates, status, remaining) is delegated to the real ledger.
"""

from __future__ import annotations

from cerebra.domain.quota import QuotaLedger


class AdapterMeteredQuota:
    holds_reservations = False

    def __init__(self, ledger: QuotaLedger) -> None:
        self._ledger = ledger

    # check-only "reservation"
    def reserve(self, general: int = 0, search: int = 0) -> bool:
        return self._ledger.can_afford(general=general, search=search)

    def can_afford(self, general: int = 0, search: int = 0) -> bool:
        return self._ledger.can_afford(general=general, search=search)

    # accounting is done by the adapter, per call
    def release(self, general: int = 0, search: int = 0) -> None:
        return None

    def consume(self, method: str, count: int = 1) -> None:
        return None

    def __getattr__(self, name: str) -> object:
        return getattr(self._ledger, name)
