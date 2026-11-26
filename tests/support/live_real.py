"""Helpers for the REAL-API live suites (manual / scheduled only; NFR-TE-1, TR-5).

Nothing here runs in PR CI: live tests are deselected by default (pyproject addopts) and
skip themselves when credentials are absent. Secrets come from the environment only
(FR-5.2, NFR-SE-2) and are never printed.
"""

from __future__ import annotations

import os
from pathlib import Path

from cerebra.config import Config
from cerebra.infrastructure.composition import Container, build_container

LIVE_WRITE_CONFIRM = "I-CONFIRM-WRITE-TO-TEST-ACCOUNT"
CONFIRM_ENV = "CEREBRA_LIVE_WRITE_CONFIRM"

# Stable, public, tiny (19 s) video: "Me at the zoo". Safe seed for read tests and the
# single item of the write test.
STABLE_PUBLIC_VIDEO = "jNQXAC9IVRw"
# Well-formed 11-char ID that does not exist.
NONEXISTENT_VIDEO = "AAAAAAAAAAA"


def live_env_missing(*, write: bool = False) -> str:
    """Return a human-readable skip reason, or "" when the live env is complete."""
    needed = ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "CEREBRA_REFRESH_TOKEN"]
    missing = [name for name in needed if not os.environ.get(name)]
    if missing:
        return "live env incomplete (missing: " + ", ".join(missing) + ")"
    if write:
        if os.environ.get(CONFIRM_ENV) != LIVE_WRITE_CONFIRM:
            return f"{CONFIRM_ENV} does not hold the exact confirmation phrase"
        if "youtube" not in os.environ.get("CEREBRA_REFRESH_TOKEN_SCOPES", ""):
            return "CEREBRA_REFRESH_TOKEN_SCOPES must declare the write scope for the write test"
    return ""


def build_live_container(data_dir: Path, *, budget: int | None = None) -> Container:
    """Real composition root, real YouTube adapter, headless credentials from the env."""
    cfg = Config()
    cfg.data_dir = data_dir
    cfg.use_keyring = False
    cfg.interactive_auth = False  # never open a browser in a live run
    cfg.token_file = data_dir / "token.json"  # unused in headless mode; keeps HOME clean
    cfg.max_candidates = min(cfg.max_candidates, 25)  # keep the quota footprint small
    if budget is not None:
        cfg.daily_quota_budget = budget
    return build_container(cfg, use_fixtures=False)
