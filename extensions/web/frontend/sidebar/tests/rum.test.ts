import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "vitest";

const PAGE = join(import.meta.dirname, "..", "sidebar.html");

test("the page declares the block the surface writes the deploy into", () => {
  const slot = '<script type="application/json" id="rum">null</script>';
  expect(readFileSync(PAGE, "utf8")).toContain(slot);
});
