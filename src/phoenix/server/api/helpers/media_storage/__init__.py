"""
Where prompt media bytes live.

Media is the only large binary Phoenix handles — every other ``LargeBinary`` column in
the schema holds a digest, a secret or a small config — so it is the one thing that
does not belong in the database. Bytes in the database inflate every backup and
snapshot, and space freed by a delete is not returned without a full vacuum.

So the bytes go to object storage and the ``media_files`` row keeps only metadata: the
type determined from the content, the size, the original name, and when the media was
uploaded and last used.

Two backends, chosen by configuration rather than by a registry, because there are two:

``PHOENIX_MEDIA_GCS_BUCKET`` set
    Google Cloud Storage. What a deployment uses.

unset
    The local filesystem, under the Phoenix working directory. What local development
    and the test suite use, so neither needs credentials or a network.

Nothing outside this package needs to know which one is active. A prompt template, a
span and a dataset example all reference media as ``phoenix://media/<sha256>`` — a
digest, never a location — so the backend can change under them, and did.
"""

from typing import Iterable, Optional, Protocol, runtime_checkable

from ._config import (
    ENV_PHOENIX_MEDIA_GCS_BUCKET,
    ENV_PHOENIX_MEDIA_GCS_PREFIX,
    ENV_PHOENIX_MEDIA_LOCAL_DIR,
    get_env_media_gcs_bucket,
    get_env_media_gcs_prefix,
    get_env_media_local_dir,
)
from ._gcs import GcsMediaStore
from ._local import LocalMediaStore


@runtime_checkable
class MediaStore(Protocol):
    """
    Somewhere content-addressed media can be put, read and deleted.

    Deliberately three methods. Media is immutable and named by its own digest, so
    there is nothing to update, nothing to list and nothing to rename.
    """

    async def put(self, sha256: str, content: bytes, *, media_type: str) -> None:
        """Store media, doing nothing if it is already stored."""
        ...

    async def get(self, sha256: str) -> Optional[bytes]:
        """Read stored media, or None if it is not there."""
        ...

    async def delete(self, digests: Iterable[str]) -> None:
        """Delete stored media, ignoring anything already gone."""
        ...


def media_store() -> MediaStore:
    """
    The configured media store.

    Resolved per call rather than cached, so a test can point the filesystem backend at
    a temporary directory with `monkeypatch.setenv` and a deployment can be reconfigured
    without a restart. The GCS client underneath *is* cached, so this costs nothing
    beyond reading two environment variables.

    Returns:
        A GCS store when a bucket is configured, otherwise a filesystem store.
    """
    if bucket := get_env_media_gcs_bucket():
        return GcsMediaStore(bucket=bucket, prefix=get_env_media_gcs_prefix())
    return LocalMediaStore(get_env_media_local_dir())


__all__ = [
    "ENV_PHOENIX_MEDIA_GCS_BUCKET",
    "ENV_PHOENIX_MEDIA_GCS_PREFIX",
    "ENV_PHOENIX_MEDIA_LOCAL_DIR",
    "GcsMediaStore",
    "LocalMediaStore",
    "MediaStore",
    "get_env_media_gcs_bucket",
    "get_env_media_gcs_prefix",
    "get_env_media_local_dir",
    "media_store",
]
