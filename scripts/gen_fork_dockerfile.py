#!/usr/bin/env python3
"""
Generate ``Dockerfile.fork`` from upstream's ``Dockerfile``.

The fork needs one extra step in the image — installing the Google Cloud Storage SDK,
which is deliberately not in ``pyproject.toml`` — and there are three ways to get it.

Editing upstream's ``Dockerfile`` in place is the obvious one, and the worst: the
insertion lands in the middle of the ``backend-builder`` stage, in a file upstream edits
often, which is the shape a merge handles worst.

Keeping a hand-maintained copy is simpler to read but rots. It duplicates ~140 lines of
upstream infrastructure — base images, the distroless final stage, the deno and WASM
runtimes — and when upstream bumps a base image for a CVE, the copy keeps the old one
and nothing fails.

So the copy is generated. The delta the fork owns is the block below and nothing else,
and because generation is anchored to a specific upstream line, an upstream change to
that region makes this script *fail* rather than quietly produce a stale image.

Usage::

    make dockerfile-fork          # regenerate
    make check-dockerfile-fork    # verify the checked-in copy is current (CI)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_DOCKERFILE = REPO_ROOT / "Dockerfile"
FORK_DOCKERFILE = REPO_ROOT / "Dockerfile.fork"

ANCHOR = "RUN uv pip install dist/*.whl --no-deps"
"""
The last line of the backend-builder stage's install sequence.

Anchored to this rather than to a line number so that edits elsewhere in the file do not
shift the insertion, and so that an upstream change to the install sequence itself is
caught here instead of silently producing something wrong.
"""

HEADER = """\
# =============================================================================
# GENERATED FILE — DO NOT EDIT
# =============================================================================
#
# Generated from ./Dockerfile by scripts/gen_fork_dockerfile.py.
#
# Upstream's Dockerfile plus one fork-only step: installing the Google Cloud Storage
# SDK. Generated rather than hand-maintained so that ~140 lines of upstream build
# infrastructure cannot drift — when upstream bumps a base image, regenerating picks it
# up, and `make check-dockerfile-fork` fails until it has been.
#
# To change the fork's delta, edit INSERTION in scripts/gen_fork_dockerfile.py.
# To pick up upstream changes, run `make dockerfile-fork`.
#
# Build:  docker build -f Dockerfile.fork -t phoenix-fork .
#     or  make docker-build-fork
#
# =============================================================================

"""

INSERTION = """\

# Fork-only: Google Cloud Storage backend for prompt media. Without this the image can
# only keep media on the local filesystem — setting PHOENIX_MEDIA_GCS_BUCKET would raise
# the "requires the google-cloud-storage package" RuntimeError from
# phoenix.server.api.helpers.media_storage._gcs at the first media write, and only then,
# because the SDK is imported lazily.
#
# Deliberately AFTER `uv sync`: sync reconciles .venv against uv.lock and would prune a
# package the lockfile does not mention. `--no-deps` is NOT used here — unlike the wheel
# above, this needs its transitive deps (google-api-core, google-auth,
# google-cloud-core, google-crc32c, proto-plus).
#
# COPY sits here rather than beside the other COPYs at the top of the stage so that
# editing the requirements file does not invalidate the cached `uv sync` layer.
COPY ./requirements/fork-gcs.txt /phoenix/requirements/fork-gcs.txt
RUN uv pip install -r requirements/fork-gcs.txt
"""


def render() -> str:
    """
    Build the fork Dockerfile's contents.

    Returns:
        The generated Dockerfile text.

    Raises:
        SystemExit: The anchor is missing or ambiguous, meaning upstream changed the
            install sequence and the insertion point needs a human decision.
    """
    if not UPSTREAM_DOCKERFILE.is_file():
        sys.exit(f"error: {UPSTREAM_DOCKERFILE} not found")

    lines = UPSTREAM_DOCKERFILE.read_text().splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.rstrip("\n") == ANCHOR]

    if not matches:
        sys.exit(
            f"error: anchor not found in {UPSTREAM_DOCKERFILE.name}:\n"
            f"  {ANCHOR!r}\n"
            f"Upstream changed the backend-builder install sequence. Pick the new "
            f"insertion point and update ANCHOR in {Path(__file__).name}."
        )
    if len(matches) > 1:
        sys.exit(
            f"error: anchor appears {len(matches)} times in "
            f"{UPSTREAM_DOCKERFILE.name}; it must be unique to place the insertion."
        )

    at = matches[0] + 1
    return HEADER + "".join(lines[:at]) + INSERTION + "".join(lines[at:])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the checked-in Dockerfile.fork is not current",
    )
    args = parser.parse_args()

    generated = render()

    if args.check:
        current = FORK_DOCKERFILE.read_text() if FORK_DOCKERFILE.is_file() else ""
        if current != generated:
            print(
                f"error: {FORK_DOCKERFILE.name} is out of date with "
                f"{UPSTREAM_DOCKERFILE.name}.\nRun `make dockerfile-fork` and commit "
                f"the result.",
                file=sys.stderr,
            )
            return 1
        print(f"{FORK_DOCKERFILE.name} is up to date")
        return 0

    FORK_DOCKERFILE.write_text(generated)
    print(f"wrote {FORK_DOCKERFILE.name} ({len(generated.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
