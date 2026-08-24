import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { ArtifactText } from "@/kernel/artifact";

test("a text artifact still reading draws the one waiting mark", () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise<Response>(() => {})),
  );

  render(<ArtifactText url="/artifacts/notes.md" name="notes.md" mediaType="text/markdown" />);

  expect(screen.getByText("Loading…").className).toBe("animate-waiting");
});
