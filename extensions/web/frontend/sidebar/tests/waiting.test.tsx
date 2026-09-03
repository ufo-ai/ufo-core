import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { ArtifactText } from "@/kernel/artifact";

test("a text artifact still reading draws the one waiting mark", () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise<Response>(() => {})),
  );

  render(<ArtifactText url="/artifacts/notes.md" name="notes.md" mediaType="text/markdown" />);

  const mark = screen.getByRole("status");
  expect(mark.className).toContain("animate-waiting");
  expect(mark.className).toContain("items-center justify-center");
  expect(mark.querySelector("svg")?.classList.contains("animate-spin")).toBe(true);
  expect(mark.textContent).toBe("Loading…");
});
