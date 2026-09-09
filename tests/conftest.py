"""Test isolation.

The suite must never make a billed API call. Once ``PARALLEL_API_KEY`` is set
in a developer's ``.env``, anything that constructs a ``ClearancePipeline()``
with ambient settings would otherwise resolve to the live research provider and
quietly spend money — and the deep processor tiers take minutes each, so the
first symptom is a test run that appears to hang.

Environment variables take precedence over ``.env`` in pydantic-settings, so
forcing them here neutralises whatever is on the machine. The settings cache is
cleared because ``get_settings`` is memoised and may already have been warmed
by an import.
"""

from __future__ import annotations

import os

import pytest

os.environ["USE_FIXTURES"] = "true"
os.environ["PARALLEL_API_KEY"] = ""
os.environ["GOOGLE_CLOUD_PROJECT"] = ""

from clearance_desk.config import get_settings  # noqa: E402

get_settings.cache_clear()


@pytest.fixture(autouse=True, scope="session")
def _offline_settings() -> None:
    settings = get_settings()
    assert not settings.parallel_enabled, (
        "tests must never reach the live research provider"
    )


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly rather than silently billing if isolation ever regresses."""

    def _blocked(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError(
            "a test attempted a live Parallel call — check test isolation"
        )

    monkeypatch.setattr(
        "clearance_desk.research.ParallelResearcher.research", _blocked, raising=True
    )
