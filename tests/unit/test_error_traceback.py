"""Regression: CerebraError must survive Python-level traceback assignment (TR-9, NFR-RE-1)."""

# mark: FR-5.15, NFR-RE-1, TR-9
from __future__ import annotations

import contextlib

import pytest

from cerebra.domain.errors import CerebraError, ErrorCode


@contextlib.contextmanager
def _passthrough():
    yield


def test_error_passes_through_contextmanager_unchanged() -> None:
    with pytest.raises(CerebraError) as err, _passthrough():
        raise CerebraError(ErrorCode.YOUTUBE_API_ERROR, "boom", retryable=True)
    assert err.value.code == ErrorCode.YOUTUBE_API_ERROR
    assert err.value.retryable is True
    assert err.value.to_dict()["error"]["message"] == "boom"


def test_traceback_can_be_set_and_error_is_hashable() -> None:
    e = CerebraError(ErrorCode.INVALID_ARGUMENT, "x")
    e.__traceback__ = None
    assert {e: 1}[e] == 1
