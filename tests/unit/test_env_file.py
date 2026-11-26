"""Tests for the optional untracked env file (FR-5.2, NFR-SE-2, NFR-SE-3)."""

# mark: FR-5.2, NFR-SE-2, NFR-SE-3
from __future__ import annotations

import os
from pathlib import Path

import pytest

from cerebra.config import _parse_env_line, env_file_candidates, load_env_file


def _write(path: Path, text: str, mode: int = 0o600) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def test_parse_lines() -> None:
    assert _parse_env_line("A=1") == ("A", "1")
    assert _parse_env_line("export B = two ") == ("B", "two")
    assert _parse_env_line('C="quoted # kept"') == ("C", "quoted # kept")
    assert _parse_env_line("D=plain # comment") == ("D", "plain")
    assert _parse_env_line("E=") == ("E", "")
    for junk in ("", "# c", "no-equals", "1BAD=x", "BA-D=x"):
        assert _parse_env_line(junk) is None


def test_loads_values_and_env_wins(tmp_path: Path) -> None:
    f = _write(tmp_path / ".env", "GOOGLE_CLIENT_ID=from-file\nGOOGLE_CLIENT_SECRET=s3\n")
    env = {"GOOGLE_CLIENT_ID": "from-env"}
    assert load_env_file(f, env) == f
    assert env["GOOGLE_CLIENT_ID"] == "from-env"
    assert env["GOOGLE_CLIENT_SECRET"] == "s3"


def test_empty_env_value_is_filled(tmp_path: Path) -> None:
    f = _write(tmp_path / ".env", "K=filled\n")
    env = {"K": ""}
    load_env_file(f, env)
    assert env["K"] == "filled"


def test_candidates_order_and_explicit(tmp_path: Path) -> None:
    explicit = tmp_path / "x.env"
    assert list(env_file_candidates({"CEREBRA_ENV_FILE": str(explicit)})) == [explicit]
    cands = list(env_file_candidates({"XDG_CONFIG_HOME": str(tmp_path)}))
    assert cands[0] == tmp_path / "cerebra" / ".env"
    assert cands[1].name == ".env"


def test_missing_file_is_noop(tmp_path: Path) -> None:
    assert load_env_file(tmp_path / "nope.env", {}) is None


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_warns_on_open_permissions_without_leaking_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = _write(tmp_path / ".env", "SECRET_VALUE_KEY=topsecret123\n", mode=0o644)
    env: dict[str, str] = {}
    load_env_file(f, env)
    err = capsys.readouterr().err
    assert "chmod 600" in err
    assert "topsecret123" not in err


def test_unreadable_encoding_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / ".env"
    f.write_bytes(b"\xff\xfe\x00bad")
    assert load_env_file(f, {}) is None
    assert "cannot read env file" in capsys.readouterr().err
