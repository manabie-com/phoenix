"""Prompt media in Google Cloud Storage."""

import logging
from functools import lru_cache
from typing import Any, Iterable, NamedTuple, Optional

from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)


class _GcsApi(NamedTuple):
    """The pieces of the GCS SDK this backend uses."""

    client: Any
    not_found: type[BaseException]
    precondition_failed: type[BaseException]


@lru_cache(maxsize=1)
def _gcs_api() -> _GcsApi:
    """
    The GCS client and the exception types that go with it, resolved once.

    Imported lazily, following how the rest of Phoenix treats optional cloud SDKs
    (see `phoenix.db.aws_auth`): the dependency is never imported unless the backend is
    actually configured, so a deployment keeping media on the filesystem does not need
    it installed.

    One import site rather than one per method, so a missing dependency always produces
    the message below rather than a bare ImportError from whichever call ran first.

    Credentials come from Application Default Credentials, which is what makes Workload
    Identity work on GKE and Cloud Run with nothing configured.

    Returns:
        The client and the ``NotFound`` / ``PreconditionFailed`` types.

    Raises:
        RuntimeError: The GCS SDK is not installed.
    """
    try:
        from google.api_core.exceptions import (  # type: ignore[import-not-found,unused-ignore]
            NotFound,
            PreconditionFailed,
        )
        from google.cloud import storage  # type: ignore[attr-defined,unused-ignore]
    except ImportError as error:
        raise RuntimeError(
            "Storing prompt media in Google Cloud Storage requires the "
            "google-cloud-storage package. Install it with "
            "`pip install -r requirements/fork-gcs.txt` (or `make install-gcs`), or "
            "unset PHOENIX_MEDIA_GCS_BUCKET to keep media on the local filesystem."
        ) from error
    return _GcsApi(
        client=storage.Client(),
        not_found=NotFound,
        precondition_failed=PreconditionFailed,
    )


class GcsMediaStore:
    """
    Media held as objects in a GCS bucket, named by digest.

    Content-addressing and object storage fit together well: objects are immutable,
    named by the digest of what they contain, and identical media uploaded twice is one
    object. Nothing here needs versioning, patching or listing.

    The SDK is synchronous, so every call runs in a worker thread. That is not a
    compromise worth an async client — media work is one upload per attachment and one
    read per run, so the thread hop sits far below the network round-trip it wraps.

    Deletion is owned by `MediaSweeper`, not by a bucket lifecycle rule. A lifecycle
    rule cannot know what a prompt version references, so it would eventually delete
    media a live prompt still points at.
    """

    def __init__(self, bucket: str, prefix: str) -> None:
        self._bucket = bucket
        self._prefix = prefix

    def _name(self, sha256: str) -> str:
        return f"{self._prefix}{sha256}"

    async def put(self, sha256: str, content: bytes, *, media_type: str) -> None:
        """
        Store media, doing nothing if the object already exists.

        Args:
            sha256: The digest naming the media.
            content: The bytes.
            media_type: Recorded as the object's content type, so anything reading the
                bucket directly sees the type Phoenix determined from the bytes.
        """

        def upload() -> None:
            api = _gcs_api()
            blob = api.client.bucket(self._bucket).blob(self._name(sha256))
            try:
                # `if_generation_match=0` means "only if absent" — one atomic request
                # rather than an exists() check that could race, and it avoids
                # re-uploading bytes the bucket already holds.
                blob.upload_from_string(
                    content,
                    content_type=media_type,
                    if_generation_match=0,
                )
            except api.precondition_failed:
                # The object exists. Content-addressed, so it holds these exact bytes.
                pass

        await run_in_threadpool(upload)

    async def get(self, sha256: str) -> Optional[bytes]:
        """
        Read stored media.

        Args:
            sha256: The digest naming the media.

        Returns:
            The bytes, or None if no object is stored under that digest.
        """

        def download() -> Optional[bytes]:
            api = _gcs_api()
            blob = api.client.bucket(self._bucket).blob(self._name(sha256))
            try:
                return bytes(blob.download_as_bytes())
            except api.not_found:
                return None

        return await run_in_threadpool(download)

    async def delete(self, digests: Iterable[str]) -> None:
        """
        Delete stored media, ignoring anything already gone.

        Args:
            digests: The digests to delete.
        """
        names = tuple(digests)
        if not names:
            return

        def remove() -> None:
            api = _gcs_api()
            bucket = api.client.bucket(self._bucket)
            for sha256 in names:
                try:
                    bucket.blob(self._name(sha256)).delete()
                except api.not_found:
                    pass
                except Exception:
                    # One unreachable object must not abandon the rest of the sweep.
                    # The metadata row goes either way, so a survivor becomes untracked
                    # — logged loudly, because from here on only the bucket owner can
                    # see it.
                    logger.exception(
                        f"Failed to delete media object {self._name(sha256)} from "
                        f"bucket {self._bucket}; it is now untracked."
                    )

        await run_in_threadpool(remove)

    def __repr__(self) -> str:
        return f"GcsMediaStore(bucket={self._bucket!r}, prefix={self._prefix!r})"
