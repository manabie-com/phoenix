"""Prompt media on the local filesystem."""

import logging
import os
import tempfile
from pathlib import Path
from typing import Iterable, Optional

from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)


class LocalMediaStore:
    """
    Media held as files under a directory.

    What local development and the test suite use, so neither needs credentials or a
    network. Also a reasonable production choice when the deployment already has a
    durable shared volume and does not want a bucket.

    Files are sharded one level deep by the first two characters of the digest. A flat
    directory would work on every filesystem Phoenix supports, but directory listings
    and some tooling degrade badly past a few tens of thousands of entries, and the
    shard costs nothing.
    """

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def _path(self, sha256: str) -> Path:
        return self._directory / sha256[:2] / sha256

    def _put(self, sha256: str, content: bytes) -> None:
        path = self._path(sha256)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written to a temporary neighbour and renamed, so a reader never sees a
        # half-written file: the digest in the name promises the full contents.
        # `os.replace` is atomic within a filesystem, which the same directory
        # guarantees.
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".partial")
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(content)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    async def put(self, sha256: str, content: bytes, *, media_type: str) -> None:
        """
        Store media, doing nothing if it is already stored.

        Args:
            sha256: The digest naming the media.
            content: The bytes.
            media_type: Ignored — a file carries no type, and the authoritative type
                lives on the ``media_files`` row.
        """
        if self._path(sha256).exists():
            # Content-addressed, so an existing file holds exactly these bytes.
            return
        await run_in_threadpool(self._put, sha256, content)

    async def get(self, sha256: str) -> Optional[bytes]:
        """
        Read stored media.

        Args:
            sha256: The digest naming the media.

        Returns:
            The bytes, or None if nothing is stored under that digest.
        """

        def read() -> Optional[bytes]:
            try:
                return self._path(sha256).read_bytes()
            except FileNotFoundError:
                return None

        return await run_in_threadpool(read)

    async def delete(self, digests: Iterable[str]) -> None:
        """
        Delete stored media, ignoring anything already gone.

        Args:
            digests: The digests to delete.
        """

        def remove(names: tuple[str, ...]) -> None:
            for sha256 in names:
                path = self._path(sha256)
                path.unlink(missing_ok=True)
                # Prune the shard directory once it empties, so a long-lived
                # deployment does not accumulate 256 permanently empty directories.
                try:
                    path.parent.rmdir()
                except OSError:
                    pass

        await run_in_threadpool(remove, tuple(digests))

    def __repr__(self) -> str:
        return f"LocalMediaStore({str(self._directory)!r})"
