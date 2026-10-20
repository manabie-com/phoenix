/**
 * Registering an overlay plane's host element without losing what it hosts.
 *
 * A plane (e.g. `DrawerPlane`) hands its element to `OverlayFrameProvider` from a
 * ref, so the element lands only after the commit that mounted it. A drawer opened
 * by a deep link — a shared trace URL — mounts in that same commit, renders before
 * the host exists, and returns nothing; it shows only once the registration
 * re-renders it.
 *
 * As an urgent update, that registration can suspend the project page into its
 * Suspense fallback while its data is still loading. React then reveals the page
 * with the drawer's earlier, host-less render and does not re-render it, so the
 * drawer never appears. The URL is right and nothing is shown, which is how shared
 * trace links broke. Registering in a transition keeps the page on screen while it
 * waits, and the drawer re-renders with its host when the transition commits.
 *
 * A fork-owned module so that `frame.tsx`, upstream's, carries one call rather than
 * the reasoning (see .claude/rules/fork-ownership.md).
 */
import { startTransition, useCallback } from "react";

/**
 * A stable setter that registers a host element in a transition.
 *
 * Stable because it is passed as a ref: a new function each render would make React
 * detach and re-attach the ref, re-registering the host on every render.
 *
 * @param setHost - The state setter that holds the host element.
 */
export function useHostRegistration<T>(
  setHost: (element: T) => void
): (element: T) => void {
  return useCallback(
    (element: T) => startTransition(() => setHost(element)),
    [setHost]
  );
}
