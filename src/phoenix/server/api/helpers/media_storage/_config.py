"""
Where prompt media is stored.

Read from the environment here rather than from `phoenix.config` deliberately. That
module belongs to upstream and is edited constantly, so every setting the fork adds to
it is a line that has to survive a merge. Nothing else needs these values, so they can
live beside the code that uses them at no cost.

Naming follows the ``PHOENIX_`` convention so the settings read like the rest of
Phoenix's configuration regardless of which module happens to parse them.
"""

import os
from pathlib import Path
from typing import Optional

from phoenix.config import get_working_dir

ENV_PHOENIX_MEDIA_GCS_BUCKET = "PHOENIX_MEDIA_GCS_BUCKET"
"""
The Google Cloud Storage bucket that holds prompt media.

Setting this selects the GCS backend. Leaving it unset selects the local filesystem,
which is what local development and the test suite use — neither should need
credentials or a network.

Media bytes are never stored in the database.
"""

ENV_PHOENIX_MEDIA_GCS_PREFIX = "PHOENIX_MEDIA_GCS_PREFIX"
"""
An object-name prefix within the bucket, so media can share a bucket with other data.

Defaults to ``media/``. A trailing slash is added if missing, since a prefix without
one silently produces names like ``mediaabc123`` rather than ``media/abc123``.
"""

ENV_PHOENIX_MEDIA_LOCAL_DIR = "PHOENIX_MEDIA_LOCAL_DIR"
"""
Where the filesystem backend keeps media.

Defaults to ``media`` inside the Phoenix working directory. Set explicitly to keep
media on a different volume from the database, or to isolate it in a test.
"""

_DEFAULT_GCS_PREFIX = "media/"


def get_env_media_gcs_bucket() -> Optional[str]:
    """
    The GCS bucket holding prompt media, if one is configured.

    Returns:
        The bucket name, or None when media should be kept on the local filesystem.
    """
    bucket = os.environ.get(ENV_PHOENIX_MEDIA_GCS_BUCKET, "").strip()
    return bucket or None


def get_env_media_gcs_prefix() -> str:
    """
    The object-name prefix for media within the bucket.

    Returns:
        The prefix, always ending in ``/`` so digests append cleanly. An explicitly
        empty value means the bucket root.
    """
    if (prefix := os.environ.get(ENV_PHOENIX_MEDIA_GCS_PREFIX)) is None:
        return _DEFAULT_GCS_PREFIX
    prefix = prefix.strip().lstrip("/")
    if not prefix:
        return ""
    return prefix if prefix.endswith("/") else f"{prefix}/"


def get_env_media_local_dir() -> Path:
    """
    The directory the filesystem backend keeps media in.

    Returns:
        The configured directory, or ``media`` under the Phoenix working directory.
    """
    if directory := os.environ.get(ENV_PHOENIX_MEDIA_LOCAL_DIR, "").strip():
        return Path(directory).resolve()
    return get_working_dir() / "media"
