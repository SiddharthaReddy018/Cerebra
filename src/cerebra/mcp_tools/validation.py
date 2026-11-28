"""MCP tool schemas and input validation (FR-5.12, FR-5.14)."""

from __future__ import annotations

import re
from typing import Any

from cerebra.domain.errors import invalid_argument

# Strict ID patterns (NFR-SE-4)
_VIDEO_ID_STRICT = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_PROPOSAL_ID_STRICT = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_DIGEST_STRICT = re.compile(r"^[0-9a-f]{64}$")
_LANGUAGE_STRICT = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")  # e.g. "en" or "en-US"

# Path-traversal and null-byte patterns (NFR-SE-4, NFR-SE-5)
_PATH_TRAVERSAL_RE = re.compile(r"\.\.[/\\]|^/|^[A-Za-z]:[/\\]|\x00")

MAX_TOPIC_LEN = 500
MAX_REFERENCE_LEN = 500


def validate_reference(ref: Any) -> str:
    """Validate a video or channel reference parameter."""
    if not isinstance(ref, str):
        raise invalid_argument("reference", "must be a string")
    ref = ref.strip()
    if not ref:
        raise invalid_argument("reference", "must not be empty")
    if len(ref) > MAX_REFERENCE_LEN:
        raise invalid_argument("reference", f"too long (max {MAX_REFERENCE_LEN} chars)")
    # Reject path-traversal patterns (NFR-SE-4)
    if _PATH_TRAVERSAL_RE.search(ref):
        raise invalid_argument("reference", "contains disallowed path characters")
    return str(ref)


def validate_limit(limit: Any, default: int = 10, max_val: int = 50) -> int:
    if limit is None:
        return default
    if not isinstance(limit, int):
        raise invalid_argument("limit", "must be an integer")
    if limit < 1 or limit > max_val:
        raise invalid_argument("limit", f"must be between 1 and {max_val}")
    return limit


def validate_topic(topic: Any) -> str:
    if not isinstance(topic, str):
        raise invalid_argument("topic", "must be a string")
    topic = topic.strip()
    if not topic:
        raise invalid_argument("topic", "must not be empty")
    if len(topic) > MAX_TOPIC_LEN:
        raise invalid_argument("topic", f"too long (max {MAX_TOPIC_LEN} chars)")
    return str(topic)


def validate_duration_seconds(val: Any, param: str) -> int:
    if not isinstance(val, int):
        raise invalid_argument(param, "must be an integer number of seconds")
    if val <= 0:
        raise invalid_argument(param, "must be positive")
    if val > 86400 * 30:  # 30 days max
        raise invalid_argument(param, "unreasonably large value")
    return val


def validate_language(lang: Any) -> str | None:
    if lang is None:
        return None
    if not isinstance(lang, str):
        raise invalid_argument("language", "must be a string")
    lang = lang.strip()
    if not lang:
        return None
    if not _LANGUAGE_STRICT.match(lang):
        raise invalid_argument("language", "must be a BCP-47 language code like 'en' or 'en-US'")
    return str(lang)


def validate_proposal_id(pid: Any) -> str:
    if not isinstance(pid, str):
        raise invalid_argument("proposal_id", "must be a string")
    pid = pid.strip()
    if not _PROPOSAL_ID_STRICT.match(pid):
        raise invalid_argument("proposal_id", "must be a valid UUID v4")
    return str(pid)


def validate_digest(digest: Any) -> str:
    if not isinstance(digest, str):
        raise invalid_argument("digest", "must be a string")
    digest = digest.strip().lower()
    if not _DIGEST_STRICT.match(digest):
        raise invalid_argument("digest", "must be a 64-character hex SHA-256 digest")
    return str(digest)


def validate_privacy_status(ps: Any) -> str:
    if ps is None:
        return "private"
    if not isinstance(ps, str):
        raise invalid_argument("privacy_status", "must be a string")
    ps = ps.strip().lower()
    if ps not in ("private", "public", "unlisted"):
        raise invalid_argument("privacy_status", "must be 'private', 'public' or 'unlisted'")
    return str(ps)


def validate_top_k(k: Any) -> int:
    if k is None:
        return 5
    if not isinstance(k, int):
        raise invalid_argument("top_k", "must be an integer")
    if k < 1 or k > 50:
        raise invalid_argument("top_k", "must be between 1 and 50")
    return k
