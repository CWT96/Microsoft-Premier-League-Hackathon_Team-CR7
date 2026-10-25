"""Generator loader: real module if it imports cleanly, otherwise the stub."""

import logging
import sys
import types

import pytest

from backend.agents import generator
from backend.agents.generator import get_propose
from backend.agents.question_generator_stub import propose as stub_propose


def test_missing_module_falls_back_to_stub(caplog) -> None:
    generator._warned_missing.clear()
    with caplog.at_level(logging.INFO):
        assert get_propose("backend.agents.no_such_generator") is stub_propose
        assert get_propose("backend.agents.no_such_generator") is stub_propose
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "using stub" in warnings[0].message


def test_default_uses_stub_while_stephens_file_is_absent() -> None:
    assert get_propose() is stub_propose


def test_real_module_is_used_when_present(monkeypatch, caplog) -> None:
    fake = types.ModuleType("fake_generator")
    fake.propose = lambda *a, **k: "real"
    monkeypatch.setitem(sys.modules, "fake_generator", fake)
    with caplog.at_level(logging.INFO):
        assert get_propose("fake_generator") is fake.propose
    assert "using fake_generator" in caplog.text


def test_module_without_propose_falls_back(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "empty_generator", types.ModuleType("empty_generator"))
    assert get_propose("empty_generator") is stub_propose


def test_module_failing_to_import_falls_back(tmp_path, monkeypatch, caplog) -> None:
    (tmp_path / "broken_generator.py").write_text("raise RuntimeError('boom')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    with caplog.at_level(logging.WARNING):
        assert get_propose("broken_generator") is stub_propose
    rec = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert rec and rec[0].exc_info and "boom" in str(rec[0].exc_info[1])


def test_module_with_missing_dependency_falls_back(tmp_path, monkeypatch, caplog) -> None:
    (tmp_path / "dep_generator.py").write_text("import not_installed_anywhere_xyz\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    with caplog.at_level(logging.WARNING):
        assert get_propose("dep_generator") is stub_propose
    rec = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert rec and "not_installed_anywhere_xyz" in str(rec[0].exc_info[1])


def test_strict_mode_reraises_broken_module(tmp_path, monkeypatch) -> None:
    (tmp_path / "strict_generator.py").write_text("raise RuntimeError('boom')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("GENERATOR_STRICT", "1")
    with pytest.raises(RuntimeError):
        get_propose("strict_generator")


def test_strict_mode_still_allows_missing_module(monkeypatch) -> None:
    monkeypatch.setenv("GENERATOR_STRICT", "1")
    assert get_propose("backend.agents.no_such_generator") is stub_propose
