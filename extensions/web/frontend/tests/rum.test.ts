import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test, vi } from "vitest";

import { heldRum, identifyRum, rumOptions, startRum, type RumDeploy } from "@/lib/rum";

vi.mock("@datadog/browser-rum", () => ({
  datadogRum: { init: vi.fn(), setUser: vi.fn() },
}));

const PAGE = join(import.meta.dirname, "..", "index.html");
const DEPLOY: RumDeploy = {
  applicationId: "1ea7beef-0000-4000-8000-000000000001",
  clientToken: "pubdeadbeef",
  site: "us5.datadoghq.com",
  env: "testing",
  version: "abc12345",
};

function stated(held: string) {
  document.head.innerHTML = `<script type="application/json" id="rum">${held}</script>`;
}

test("the page declares the block the surface writes the deploy into", () => {
  const slot = '<script type="application/json" id="rum">null</script>';
  expect(readFileSync(PAGE, "utf8")).toContain(slot);
});

test("a deploy that records nothing hands back nothing to start", () => {
  stated("null");
  expect(heldRum()).toBeNull();
});

test("a deploy that records hands back what it stated", () => {
  stated(JSON.stringify(DEPLOY));
  expect(heldRum()).toEqual(DEPLOY);
});

test("a block missing a field names the field rather than recording against half of one", () => {
  const { clientToken: _dropped, ...half } = DEPLOY;
  stated(JSON.stringify(half));
  expect(() => heldRum()).toThrow("clientToken");
});

test("a page carrying no block records nothing, since no deploy serves one", () => {
  document.head.innerHTML = "";
  expect(heldRum()).toBeNull();
});

test("every field a member types into is masked in the recording", () => {
  const options = rumOptions(DEPLOY);
  expect(options.defaultPrivacyLevel).toBe("mask-user-input");
  expect(options.sessionReplaySampleRate).toBe(100);
  expect(options).toMatchObject({ env: "testing", version: "abc12345", service: "ufo-portal" });
});

test("a member named before the recorder starts is who the recording opens with", async () => {
  const { datadogRum } = await import("@datadog/browser-rum");
  identifyRum("member@example.com");
  startRum(DEPLOY);

  await vi.waitFor(() =>
    expect(datadogRum.setUser).toHaveBeenCalledWith({
      id: "member@example.com",
      email: "member@example.com",
    }),
  );
});
