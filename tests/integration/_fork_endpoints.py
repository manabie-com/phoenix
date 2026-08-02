"""Endpoint-coverage entries for REST routes that exist only in this fork.

`_helpers.py` runs `_ensure_endpoint_coverage_is_exhaustive()` at import time: every
route registered on the v1 router must appear in one of its coverage constants, or
importing the integration suite fails outright. Those constants also drive the
role-based access-control assertions, so a route missing from them is not merely
unlisted -- it has *no* authorization coverage.

The fork's media router adds three routes, which means `_helpers.py` cannot be left
untouched. Declaring them here instead keeps upstream's constants byte-identical and
reduces the fork's footprint in that file to a fixed three-line hook that does not
grow: a new fork endpoint is added to this file only.

This module is fork-owned and has no upstream counterpart, so it cannot conflict.

Format matches upstream's constants: (expected_status_code, method, endpoint_path).
"""

# GET routes every role may read.
#
# `v1/media/{sha256}` declares `pattern=r"^[0-9a-f]{64}$"` on its path parameter, so a
# non-digest id fails FastAPI's Path validation with 422 before the handler's 404
# lookup is ever reached -- hence 422 rather than the 404 a plain id route would give.
FORK_COMMON_RESOURCE_ENDPOINTS = ((422, "GET", "v1/media/fake-id-{}"),)

# Writes blocked for viewers. `restrict_access_by_viewers` is applied to the whole v1
# router and exempts only GET, so both media writes are viewer-blocked (403 for
# viewers) and answer 422 for everyone else when called without a valid body.
FORK_VIEWER_BLOCKED_WRITE_OPERATIONS = (
    (422, "POST", "v1/media"),
    (422, "POST", "v1/media/import"),
)
