"""
Keeps media written by tests out of the real working directory.

Media bytes live in a store rather than in the database, and with no GCS bucket
configured that store is the local filesystem under `PHOENIX_WORKING_DIR` — which
defaults to ``~/.phoenix``. Nothing in the suite isolates that directory, so without
this fixture every test that uploads media would write into the developer's own Phoenix
installation and leave it there.

An autouse fixture rather than a conftest entry: `tests/unit/conftest.py` belongs to
upstream, and a fixture imported into a test module applies to that module just as well.
Import it wherever media is stored::

    from tests.unit.media_store_fixtures import isolated_media_store  # noqa: F401
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from phoenix.server.api.helpers.media_storage import (
    ENV_PHOENIX_MEDIA_GCS_BUCKET,
    ENV_PHOENIX_MEDIA_LOCAL_DIR,
)


@pytest.fixture(autouse=True)
def isolated_media_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """
    Point the media store at a temporary directory for the duration of a test.

    Also clears any GCS bucket from the environment, so a developer who exports
    ``PHOENIX_MEDIA_GCS_BUCKET`` in their shell does not silently run the suite against
    a real bucket.

    Yields:
        The directory media is stored in, for a test that wants to inspect it.
    """
    directory = tmp_path / "media"
    monkeypatch.delenv(ENV_PHOENIX_MEDIA_GCS_BUCKET, raising=False)
    monkeypatch.setenv(ENV_PHOENIX_MEDIA_LOCAL_DIR, str(directory))
    yield directory
