import { expect, test } from "vitest";

import {
  ADMIN_DISCLOSURE,
  audienceDetail,
  audienceLabel,
  ownerLabel,
  speakerName,
  surfaceWord,
} from "@/lib/audience";

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
});

test("the detail names the readers and states what an admin can still do", () => {
  const said = (audience: string, member_email: string | null, surface_label?: string) =>
    audienceDetail({ audience, member_email, surface_label: surface_label ?? null }, VIEWER);

  expect(said("shared", null)).toBe(
    "Every member of the workspace reads this conversation. " + ADMIN_DISCLOSURE,
  );
  expect(said("member:m1", VIEWER)).toBe("Only you read this conversation. " + ADMIN_DISCLOSURE);
  expect(said("member:m2", "mel@example.com")).toBe(
    "Only mel@example.com reads this conversation. " + ADMIN_DISCLOSURE,
  );
  expect(said("room:C1", null, "#ops")).toBe(
    "Everyone in #ops reads this conversation. " + ADMIN_DISCLOSURE,
  );
  expect(said("room:C1", null)).toBe(
    "Everyone in the channel reads this conversation. " + ADMIN_DISCLOSURE,
  );
  expect(said("foreign:org", null)).toBe(
    "Another organization reads this conversation. " + ADMIN_DISCLOSURE,
  );
  expect(said("queue:q1", null)).toBe(
    "Who reads this conversation is not known. " + ADMIN_DISCLOSURE,
  );
});

test("a surface the map does not name reads as its own word", () => {
  expect(surfaceWord("web")).toBe("Portal");
  expect(surfaceWord("slack")).toBe("Slack");
  expect(surfaceWord("ufo")).toBe("Terminal");
  expect(surfaceWord("teams")).toBe("teams");
});

test("a reported speaker reads as the name the surface named them under", () => {
  expect(speakerName("Rae Whitlock (rae@example.com)")).toBe("Rae Whitlock");
  expect(speakerName("sam@example.com")).toBe("sam");
  expect(speakerName("Guest")).toBe("Guest");
});
