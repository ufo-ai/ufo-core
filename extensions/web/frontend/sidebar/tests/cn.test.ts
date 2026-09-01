import { expect, test } from "vitest";
import { cn } from "@/lib/cn";

test("a size and a colour spelled text-* both survive", () => {
  const said = cn("text-label", "text-ink-soft");
  expect(said).toContain("text-label");
  expect(said).toContain("text-ink-soft");
});

test("two sizes still conflict, and the last one wins", () => {
  expect(cn("text-label", "text-title")).toBe("text-title");
});
