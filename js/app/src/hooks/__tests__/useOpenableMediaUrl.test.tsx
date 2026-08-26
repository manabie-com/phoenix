import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import { useOpenableMediaUrl } from "../useOpenableMediaUrl";

let container: HTMLDivElement;
let root: Root;
let createObjectURL: ReturnType<typeof vi.fn>;
let revokeObjectURL: ReturnType<typeof vi.fn>;

/** Reports what the hook returned, so an assertion can read it out of the DOM. */
function Probe({ url }: { url: string }) {
  return <span data-testid="href">{useOpenableMediaUrl(url)}</span>;
}

function render(url: string) {
  act(() => {
    root.render(<Probe url={url} />);
  });
  return container.querySelector('[data-testid="href"]')?.textContent;
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  createObjectURL = vi.fn(() => "blob:mock-url");
  revokeObjectURL = vi.fn();
  // jsdom implements neither, and the point of the hook is which one it calls.
  Object.assign(URL, { createObjectURL, revokeObjectURL });
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

describe("useOpenableMediaUrl", () => {
  it("leaves a resolved media path alone", () => {
    expect(render("/v1/media/abc123")).toBe("/v1/media/abc123");
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it("leaves an ordinary URL alone", () => {
    expect(render("https://example.com/paper.pdf")).toBe(
      "https://example.com/paper.pdf"
    );
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it("swaps inline media for a blob URL the browser will navigate to", () => {
    expect(render("data:application/pdf;base64,JVBERi0=")).toBe(
      "blob:mock-url"
    );
    const [blob] = createObjectURL.mock.calls[0] as [Blob];
    expect(blob.type).toBe("application/pdf");
  });

  it("releases the blob when the tile goes away", () => {
    render("data:application/pdf;base64,JVBERi0=");
    expect(revokeObjectURL).not.toHaveBeenCalled();
    act(() => {
      root.render(null);
    });
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
  });
});
