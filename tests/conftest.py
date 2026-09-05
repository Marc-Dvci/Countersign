"""Shared fixtures.

Every test runs against the seeded corpus and a throwaway database. Nothing
here touches the network, and nothing invokes a model.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from countersign.config import Settings
from countersign.connectors import build_connectors
from countersign.database import Store
from countersign.service import Countersign

AS_OF = date(2026, 9, 1)
QUARTER = (AS_OF - timedelta(days=91), AS_OF)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_path=tmp_path / "countersign.db",
        session_path=tmp_path / "sessions",
        model_mode="demo",
        allow_live_connectors=False,
        as_of=AS_OF,
    )


@pytest.fixture
def store(settings) -> Store:
    return Store(settings.database_path)


@pytest.fixture
def connectors():
    return build_connectors("kestrel", allow_live=False)


@pytest.fixture
def app(settings) -> Countersign:
    return Countersign(settings)


@pytest.fixture
def seeded(app) -> Countersign:
    """A fully onboarded, approved and executed Kestrel Pay."""
    app.seed("kestrel")
    return app
