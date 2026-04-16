"""Shared fixtures for the transition-checker test suite."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest


DATA_DIR = Path(__file__).parent / "data"


@pytest.fixture
def rules_simple() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((DATA_DIR / "rules_simple.json").read_text()))


@pytest.fixture
def plan_valid() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((DATA_DIR / "plan_valid.json").read_text()))


@pytest.fixture
def plan_missing_prereq() -> dict[str, Any]:
    return cast(
        dict[str, Any], json.loads((DATA_DIR / "plan_missing_prereq.json").read_text())
    )


@pytest.fixture
def offerings_simple() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((DATA_DIR / "offerings_simple.json").read_text()))
