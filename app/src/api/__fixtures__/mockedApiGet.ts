import type { Mock } from "vitest";
import { vi } from "vitest";

import { authApiFetch } from "@phoenix/api/authApiFetch";

/**
 * A loosely-typed handle on the mocked `authApiFetch.GET`.
 *
 * Drop-in replacement for `vi.mocked(authApiFetch.GET)` in tests that stub REST
 * responses. Use it instead of `vi.mocked(...)` in any test that mocks this client.
 *
 * Why this exists — and why it is a fork concern rather than an upstream one:
 * `authApiFetch` is `createClient<paths>()`, so `GET` is generic over every GET path
 * in the OpenAPI schema. `vi.mocked(authApiFetch.GET).mockResolvedValueOnce({...})`
 * leaves TypeScript to instantiate that generic from an object literal alone, and it
 * resolves to whichever arm of the path union it happens to pick. The fork's media
 * endpoints enlarge that union, which shifts the pick and makes upstream's literals
 * fail with TS2353 ("'data' does not exist in type ...") — an error that appears in
 * upstream's test files even though upstream's own CI is green.
 *
 * These tests assert on the *request*, never on response typing, so the mock value is
 * supplied loosely, in one place, instead of being re-annotated at each call site.
 *
 * Keeping this as a function (rather than a captured const) is deliberate: suites that
 * call `vi.restoreAllMocks()` in `afterEach` would leave a captured reference stale.
 */
export function mockedApiGet(): Mock {
  return vi.mocked(authApiFetch.GET) as unknown as Mock;
}
