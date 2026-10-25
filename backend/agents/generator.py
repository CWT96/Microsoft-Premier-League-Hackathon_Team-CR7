"""generator.py — pick Stephen's question generator if it exists, else the stub.

Last Modified: 2026-10-24 23:30, first version
Inputs:  backend/agents/question_generator.py (optional)
Outputs: the ``propose`` callable; logs which generator was chosen

Workflow:
  - Import ``backend.agents.question_generator`` and use its ``propose``.
  - If the module is missing, fails to import or has no ``propose``, use the stub. A missing
    file warns once; a broken one logs an error with traceback. GENERATOR_STRICT=1 re-raises
    broken-module errors instead of falling back.

CAUTION: any import-time failure of Stephen's module is logged and falls back, so the demo loop
         keeps running; read the log line to see why the real generator was skipped.
"""

from __future__ import annotations

import importlib
import logging
import os
from collections.abc import Callable

from backend.agents.question_generator_stub import propose as stub_propose

logger = logging.getLogger(__name__)

REAL_MODULE = "backend.agents.question_generator"
STRICT_ENV = "GENERATOR_STRICT"
_warned_missing: set[str] = set()


def get_propose(module_name: str = REAL_MODULE) -> Callable:
    """Return ``propose`` from the real generator module, or the stub's.

    Args:
        module_name: module to try first (overridable for tests).

    Returns:
        A callable ``propose(state, moment, user_player_id, ...) -> Question | None``.
    """
    strict = os.environ.get(STRICT_ENV) == "1"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:  # the file is simply not there yet
            if module_name not in _warned_missing:
                _warned_missing.add(module_name)
                logger.warning("generator: %s not found, using stub", module_name)
            return stub_propose
        logger.error("generator: %s failed to import, using stub", module_name, exc_info=True)
        if strict:
            raise
        return stub_propose
    except Exception:  # noqa: BLE001 - any import-time failure must fall back, logged
        logger.error("generator: %s failed to import, using stub", module_name, exc_info=True)
        if strict:
            raise
        return stub_propose
    propose = getattr(module, "propose", None)
    if not callable(propose):
        logger.error("generator: %s has no propose(), using stub", module_name)
        if strict:
            raise ImportError(f"{module_name} has no propose()")
        return stub_propose
    logger.info("generator: using %s", module_name)
    return propose
