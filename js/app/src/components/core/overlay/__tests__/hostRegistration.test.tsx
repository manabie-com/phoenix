/**
 * A drawer opened by a deep link shows, even when registering its host suspends the
 * page around it.
 *
 * Shared trace links (`/projects/:id/spans/:traceId?timeRangeKey=…`) opened the span
 * list with no trace drawer: the drawer rendered before its host existed, the host's
 * registration suspended the project page, and the page came back with the drawer's
 * host-less render. See `hostRegistration.ts`.
 *
 * A new test file on purpose (see .claude/rules/fork-ownership.md).
 */
import { act, Suspense } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { Drawer } from "../Drawer";
import { DrawerPlane, OverlayFrameProvider, useOverlayFrame } from "../frame";

type PageData = {
  promise: Promise<void>;
  resolve: () => void;
  isLoaded: () => boolean;
};

function pageData(): PageData {
  let loaded = false;
  let resolve = () => {};
  const promise = new Promise<void>((done) => {
    resolve = () => {
      loaded = true;
      done();
    };
  });
  return { promise, resolve, isLoaded: () => loaded };
}

/**
 * Stands in for the project page: it re-renders when the host is registered, and
 * suspends then until its data has loaded.
 */
function ProjectPage({ data }: { data: PageData }) {
  const frame = useOverlayFrame();
  if (frame?.drawerHostElement && !data.isLoaded()) {
    throw data.promise;
  }
  return <div>span list</div>;
}

/** Stands in for `TracePage`, rendered through the project page's `<Outlet />`. */
function TracePage() {
  return (
    <Drawer isOpen>
      <div>trace details</div>
    </Drawer>
  );
}

/** The app's nesting: the drawer in the outlet's own boundary, inside the page's. */
function App({ data }: { data: PageData }) {
  return (
    <OverlayFrameProvider>
      <Suspense fallback={<div>loading page</div>}>
        <ProjectPage data={data} />
        <Suspense>
          <TracePage />
        </Suspense>
      </Suspense>
      {/* Mounted after the drawer, as in the app: its host lands after the commit
          the drawer first rendered in. */}
      <DrawerPlane />
    </OverlayFrameProvider>
  );
}

describe("a drawer opened by a deep link", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it("shows once the page it opened over has loaded", async () => {
    const data = pageData();
    await act(async () => {
      root.render(<App data={data} />);
    });
    await act(async () => {
      data.resolve();
      await data.promise;
    });
    expect(container.textContent).toContain("span list");
    expect(container.textContent).toContain("trace details");
  });

  it("keeps the page on screen while the registration waits", async () => {
    const data = pageData();
    await act(async () => {
      root.render(<App data={data} />);
    });
    expect(container.textContent).toContain("span list");
    expect(container.textContent).not.toContain("loading page");
    await act(async () => {
      data.resolve();
      await data.promise;
    });
  });
});
