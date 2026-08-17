import { expect, test } from "vitest";

import { audienceLabel, ownerLabel, subjectLabel, surfaceWord } from "@/lib/audience";

const VIEWER = "member@example.com";

test("an owner reads as You, the address, or the workspace", () => {
  expect(ownerLabel(VIEWER, VIEWER)).toBe("You");
  expect(ownerLabel("mel@example.com", VIEWER)).toBe("mel@example.com");
  expect(ownerLabel(null, VIEWER)).toBe("Workspace");
});

test("an audience reads as who may see it, and the viewer's own is not named twice", () => {
  const said = (audience: string, member_email: string | null, surface_label?: string) =>
    audienceLabel({ audience, member_email, surface_label: surface_label ?? null }, VIEWER);

  expect(said("shared", null)).toBe("Workspace");
  expect(said("member:m1", VIEWER)).toBe("Only you");
  expect(said("member:m1", null)).toBe("Only you");
  expect(said("member:m2", "mel@example.com")).toBe("Private to mel@example.com");
  expect(said("room:C1", null, "#ops")).toBe("#ops");
  expect(said("room:C1", null)).toBe("Private channel");
  expect(said("foreign:org", null)).toBe("Shared with another org");
});

test("an audience the map does not know reads as Unknown, never as nothing", () => {
  expect(audienceLabel({ audience: "queue:q1", member_email: null }, VIEWER)).toBe("Unknown");
  expect(subjectLabel("queue:q1")).toBe("Unknown");
  expect(subjectLabel(null)).toBe("Unknown");
  expect(subjectLabel("shared")).toBe("Workspace");
  expect(subjectLabel("member:m1")).toBe("Only you");
});

test("a surface the map does not name reads as its own word", () => {
  expect(surfaceWord("web")).toBe("Portal");
  expect(surfaceWord("slack")).toBe("Slack");
  expect(surfaceWord("ufo")).toBe("Terminal");
  expect(surfaceWord("teams")).toBe("teams");
});
