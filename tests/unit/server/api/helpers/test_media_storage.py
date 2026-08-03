"""
Where prompt media bytes are kept.

The GCS tests fake the SDK rather than reaching a bucket. What is worth pinning here is
Phoenix's own behaviour — how objects are named, that an upload is conditional so
identical bytes are not re-sent, and that a missing object reads as absent rather than
raising. Whether the real SDK honours ``if_generation_match`` is Google's contract, and
a unit test could only restate it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator, Optional

import pytest

from phoenix.server.api.helpers.media_storage import (
    ENV_PHOENIX_MEDIA_GCS_BUCKET,
    ENV_PHOENIX_MEDIA_GCS_PREFIX,
    ENV_PHOENIX_MEDIA_LOCAL_DIR,
    GcsMediaStore,
    LocalMediaStore,
    get_env_media_gcs_prefix,
    get_env_media_local_dir,
    media_store,
)
from phoenix.server.api.helpers.media_storage import _gcs as gcs_module
from tests.unit.media_store_fixtures import isolated_media_store  # noqa: F401

_DIGEST = "a" * 64
_OTHER = "b" * 64
_BYTES = b"\x89PNG\r\n\x1a\n pretend png"


# --------------------------------------------------------------------------------------
# backend selection
# --------------------------------------------------------------------------------------


class TestBackendSelection:
    def test_a_configured_bucket_selects_gcs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_PHOENIX_MEDIA_GCS_BUCKET, "phoenix-media")
        store = media_store()
        assert isinstance(store, GcsMediaStore)

    def test_no_bucket_selects_the_filesystem(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """What local development and this suite use — no credentials, no network."""
        monkeypatch.delenv(ENV_PHOENIX_MEDIA_GCS_BUCKET, raising=False)
        assert isinstance(media_store(), LocalMediaStore)

    def test_a_blank_bucket_is_treated_as_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty env var in a deployment manifest must not select a nameless bucket."""
        monkeypatch.setenv(ENV_PHOENIX_MEDIA_GCS_BUCKET, "   ")
        assert isinstance(media_store(), LocalMediaStore)


class TestConfig:
    @pytest.mark.parametrize(
        "configured,expected",
        [
            pytest.param(None, "media/", id="default"),
            pytest.param("media", "media/", id="slash-added"),
            pytest.param("media/", "media/", id="slash-kept"),
            pytest.param("a/b", "a/b/", id="nested"),
            pytest.param("/media", "media/", id="leading-slash-stripped"),
            pytest.param("", "", id="explicit-root"),
        ],
    )
    def test_prefix_always_ends_in_a_slash(
        self,
        monkeypatch: pytest.MonkeyPatch,
        configured: Optional[str],
        expected: str,
    ) -> None:
        """
        A prefix without a trailing slash would silently produce ``mediaabc123``.
        """
        if configured is None:
            monkeypatch.delenv(ENV_PHOENIX_MEDIA_GCS_PREFIX, raising=False)
        else:
            monkeypatch.setenv(ENV_PHOENIX_MEDIA_GCS_PREFIX, configured)
        assert get_env_media_gcs_prefix() == expected

    def test_local_dir_defaults_under_the_working_directory(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.delenv(ENV_PHOENIX_MEDIA_LOCAL_DIR, raising=False)
        monkeypatch.setenv("PHOENIX_WORKING_DIR", str(tmp_path))
        assert get_env_media_local_dir() == tmp_path / "media"

    def test_local_dir_can_be_set_explicitly(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """So media can sit on a different volume from the database."""
        monkeypatch.setenv(ENV_PHOENIX_MEDIA_LOCAL_DIR, str(tmp_path / "elsewhere"))
        assert get_env_media_local_dir() == tmp_path / "elsewhere"


# --------------------------------------------------------------------------------------
# filesystem backend
# --------------------------------------------------------------------------------------


@pytest.fixture
def local_store(tmp_path: Path) -> LocalMediaStore:
    return LocalMediaStore(tmp_path / "media")


class TestLocalMediaStore:
    async def test_round_trips(self, local_store: LocalMediaStore) -> None:
        await local_store.put(_DIGEST, _BYTES, media_type="image/png")
        assert await local_store.get(_DIGEST) == _BYTES

    async def test_missing_media_reads_as_absent(self, local_store: LocalMediaStore) -> None:
        """None rather than an exception, so callers decide what a 404 means."""
        assert await local_store.get(_DIGEST) is None

    async def test_putting_twice_is_a_noop(self, local_store: LocalMediaStore) -> None:
        await local_store.put(_DIGEST, _BYTES, media_type="image/png")
        await local_store.put(_DIGEST, _BYTES, media_type="image/png")
        assert await local_store.get(_DIGEST) == _BYTES

    async def test_deletes(self, local_store: LocalMediaStore) -> None:
        await local_store.put(_DIGEST, _BYTES, media_type="image/png")
        await local_store.delete([_DIGEST])
        assert await local_store.get(_DIGEST) is None

    async def test_deleting_absent_media_is_not_an_error(
        self,
        local_store: LocalMediaStore,
    ) -> None:
        """The sweeper retries a batch whose bytes already went."""
        await local_store.delete([_DIGEST, _OTHER])

    async def test_deletes_only_what_it_is_asked_to(self, local_store: LocalMediaStore) -> None:
        await local_store.put(_DIGEST, _BYTES, media_type="image/png")
        await local_store.put(_OTHER, b"other", media_type="image/png")
        await local_store.delete([_DIGEST])
        assert await local_store.get(_DIGEST) is None
        assert await local_store.get(_OTHER) == b"other"

    async def test_shards_by_digest_prefix(self, tmp_path: Path) -> None:
        """Directory listings and tooling degrade badly past tens of thousands of files."""
        directory = tmp_path / "media"
        store = LocalMediaStore(directory)
        await store.put(_DIGEST, _BYTES, media_type="image/png")
        assert (directory / _DIGEST[:2] / _DIGEST).read_bytes() == _BYTES

    async def test_leaves_no_partial_files_behind(self, tmp_path: Path) -> None:
        """
        Written to a temporary neighbour and renamed, so a reader never sees a
        half-written file — the digest in the name promises the full contents.
        """
        directory = tmp_path / "media"
        store = LocalMediaStore(directory)
        await store.put(_DIGEST, _BYTES, media_type="image/png")
        assert list(directory.rglob("*.partial")) == []

    async def test_prunes_the_shard_directory_once_empty(self, tmp_path: Path) -> None:
        directory = tmp_path / "media"
        store = LocalMediaStore(directory)
        await store.put(_DIGEST, _BYTES, media_type="image/png")
        await store.delete([_DIGEST])
        assert not (directory / _DIGEST[:2]).exists()

    async def test_creates_its_directory_on_demand(self, tmp_path: Path) -> None:
        store = LocalMediaStore(tmp_path / "does" / "not" / "exist" / "yet")
        await store.put(_DIGEST, _BYTES, media_type="image/png")
        assert await store.get(_DIGEST) == _BYTES


# --------------------------------------------------------------------------------------
# GCS backend
# --------------------------------------------------------------------------------------


class _FakeNotFound(Exception):
    pass


class _FakePreconditionFailed(Exception):
    pass


class _FakeBlob:
    def __init__(self, bucket: "_FakeBucket", name: str) -> None:
        self._bucket = bucket
        self._name = name

    def upload_from_string(
        self,
        content: bytes,
        *,
        content_type: str,
        if_generation_match: Optional[int] = None,
    ) -> None:
        self._bucket.uploads.append((self._name, content_type, if_generation_match))
        if if_generation_match == 0 and self._name in self._bucket.objects:
            raise _FakePreconditionFailed(self._name)
        self._bucket.objects[self._name] = content

    def download_as_bytes(self) -> bytes:
        try:
            return self._bucket.objects[self._name]
        except KeyError:
            raise _FakeNotFound(self._name)

    def delete(self) -> None:
        self._bucket.deletes.append(self._name)
        if self._bucket.delete_error is not None:
            raise self._bucket.delete_error
        try:
            del self._bucket.objects[self._name]
        except KeyError:
            raise _FakeNotFound(self._name)


class _FakeBucket:
    def __init__(self, name: str) -> None:
        self.name = name
        self.objects: dict[str, bytes] = {}
        self.uploads: list[tuple[str, str, Optional[int]]] = []
        self.deletes: list[str] = []
        self.delete_error: Optional[BaseException] = None

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(self, name)


class _FakeClient:
    def __init__(self) -> None:
        self.buckets: dict[str, _FakeBucket] = {}

    def bucket(self, name: str) -> _FakeBucket:
        return self.buckets.setdefault(name, _FakeBucket(name))


@pytest.fixture
def fake_gcs(monkeypatch: pytest.MonkeyPatch) -> Iterator[_FakeClient]:
    """Stand in for the GCS SDK, which is an optional dependency and not installed here."""
    client = _FakeClient()
    monkeypatch.setattr(
        gcs_module,
        "_gcs_api",
        lambda: gcs_module._GcsApi(
            client=client,
            not_found=_FakeNotFound,
            precondition_failed=_FakePreconditionFailed,
        ),
    )
    yield client


def _bucket(client: _FakeClient) -> _FakeBucket:
    return client.bucket("phoenix-media")


class TestGcsMediaStore:
    @pytest.fixture
    def store(self) -> GcsMediaStore:
        return GcsMediaStore(bucket="phoenix-media", prefix="media/")

    async def test_round_trips(self, store: GcsMediaStore, fake_gcs: _FakeClient) -> None:
        await store.put(_DIGEST, _BYTES, media_type="image/png")
        assert await store.get(_DIGEST) == _BYTES

    async def test_names_the_object_by_digest_under_the_prefix(
        self,
        store: GcsMediaStore,
        fake_gcs: _FakeClient,
    ) -> None:
        await store.put(_DIGEST, _BYTES, media_type="image/png")
        assert set(_bucket(fake_gcs).objects) == {f"media/{_DIGEST}"}

    async def test_an_empty_prefix_writes_to_the_bucket_root(
        self,
        fake_gcs: _FakeClient,
    ) -> None:
        store = GcsMediaStore(bucket="phoenix-media", prefix="")
        await store.put(_DIGEST, _BYTES, media_type="image/png")
        assert set(_bucket(fake_gcs).objects) == {_DIGEST}

    async def test_records_the_detected_media_type_as_content_type(
        self,
        store: GcsMediaStore,
        fake_gcs: _FakeClient,
    ) -> None:
        """So anything reading the bucket directly sees the type Phoenix determined."""
        await store.put(_DIGEST, _BYTES, media_type="image/png")
        assert _bucket(fake_gcs).uploads[0][1] == "image/png"

    async def test_uploads_conditionally(
        self,
        store: GcsMediaStore,
        fake_gcs: _FakeClient,
    ) -> None:
        """`if_generation_match=0` is "only if absent" — atomic, and no wasted egress."""
        await store.put(_DIGEST, _BYTES, media_type="image/png")
        assert _bucket(fake_gcs).uploads[0][2] == 0

    async def test_re_uploading_identical_bytes_is_tolerated(
        self,
        store: GcsMediaStore,
        fake_gcs: _FakeClient,
    ) -> None:
        """
        The conditional upload fails when the object exists. Content-addressed, so it
        holds exactly these bytes and the failure is the expected outcome, not an error.
        """
        await store.put(_DIGEST, _BYTES, media_type="image/png")
        await store.put(_DIGEST, _BYTES, media_type="image/png")
        assert await store.get(_DIGEST) == _BYTES
        assert len(_bucket(fake_gcs).uploads) == 2

    async def test_missing_media_reads_as_absent(
        self,
        store: GcsMediaStore,
        fake_gcs: _FakeClient,
    ) -> None:
        assert await store.get(_DIGEST) is None

    async def test_deletes(self, store: GcsMediaStore, fake_gcs: _FakeClient) -> None:
        await store.put(_DIGEST, _BYTES, media_type="image/png")
        await store.delete([_DIGEST])
        assert _bucket(fake_gcs).objects == {}

    async def test_deleting_absent_media_is_not_an_error(
        self,
        store: GcsMediaStore,
        fake_gcs: _FakeClient,
    ) -> None:
        await store.delete([_DIGEST])

    async def test_no_digests_makes_no_calls(
        self,
        store: GcsMediaStore,
        fake_gcs: _FakeClient,
    ) -> None:
        await store.delete([])
        assert fake_gcs.buckets == {}

    async def test_one_failed_delete_does_not_abandon_the_batch(
        self,
        store: GcsMediaStore,
        fake_gcs: _FakeClient,
    ) -> None:
        """
        The metadata rows go either way, so a survivor becomes untracked. Better to
        strand one object than to leave the whole sweep undone every hour.
        """
        _bucket(fake_gcs).delete_error = RuntimeError("transient")
        await store.delete([_DIGEST, _OTHER])
        assert _bucket(fake_gcs).deletes == [f"media/{_DIGEST}", f"media/{_OTHER}"]

    def test_repr_names_the_bucket(self, store: GcsMediaStore) -> None:
        assert "phoenix-media" in repr(store)


class TestGcsWithoutTheSdk:
    def test_reports_how_to_fix_a_missing_dependency(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Resolved at one import site, so a missing dependency always produces this
        message rather than a bare ImportError from whichever call happened to run.
        """
        import builtins

        real_import = builtins.__import__

        def refuse_google(name: str, *args: Any, **kwargs: Any) -> Any:
            if name.startswith("google."):
                raise ImportError(f"No module named {name!r}")
            return real_import(name, *args, **kwargs)

        gcs_module._gcs_api.cache_clear()
        monkeypatch.setattr(builtins, "__import__", refuse_google)
        try:
            with pytest.raises(RuntimeError, match=r"requirements/fork-gcs\.txt"):
                gcs_module._gcs_api()
        finally:
            monkeypatch.undo()
            gcs_module._gcs_api.cache_clear()
