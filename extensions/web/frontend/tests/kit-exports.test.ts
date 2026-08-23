import { expect, test } from "vitest";

/** The kit is served unhashed and versions with the portal deploy, so every deployed page — bytes
 *  the portal cannot rebuild — calls whatever of these it was written against. Removing or renaming
 *  one blanks every page that calls it, fleet-wide, the moment the portal rolls; this pin is where
 *  that removal fails instead. */
const DEPLOYED_PAGE_EXPORTS = [
  "compile",
  "connect",
  "founded",
  "getJson",
  "installShims",
  "mountApp",
  "navigate",
  "onOpenTarget",
  "onPlaced",
  "run",
  "SectionApp",
];

test("the kit keeps every export a deployed page calls", async () => {
  const kit = await import("@/apps/kit");
  for (const name of DEPLOYED_PAGE_EXPORTS) {
    expect(kit, name).toHaveProperty(name);
    expect((kit as Record<string, unknown>)[name], name).toBeDefined();
  }
});
