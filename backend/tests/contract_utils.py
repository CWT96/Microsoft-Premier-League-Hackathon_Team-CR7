"""Validate payloads against ``contracts/*.schema.json`` in tests."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict[str, Any]:
    """Load ``contracts/<name>.schema.json``."""
    return json.loads((CONTRACTS_DIR / f"{name}.schema.json").read_text())


def validate(name: str, payload: Any) -> None:
    """Raise ``jsonschema.ValidationError`` if ``payload`` breaks the named contract."""
    jsonschema.validate(payload, load_schema(name))
