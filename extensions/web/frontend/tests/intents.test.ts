import { beforeEach, expect, test, vi } from "vitest";

import { postIntent } from "@/lib/api";

const AGENT_ID = "0d9a5d4c-2f3e-4a41-9f7f-4f6ef7bcbb31";

beforeEach(() => {
  vi.unstubAllGlobals();
});

test("a refusal reaches the member in the words that refused it", async () => {
  /** Every fence between an app page and the intents lane answers in plain text: the bridge's
   *  endpoint table, its verb table, a surface that refused the request outright. Read as JSON they
   *  all become "Error 400 — try again.", which tells a member nothing and tells them to do the one
   *  thing that cannot work. */
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response("This intent is not available from an app page.", { status: 400 }),
    ),
  );
  expect(await postIntent(AGENT_ID, { verb: "add_member" })).toEqual({
    applied: false,
    message: "This intent is not available from an app page.",
  });
});

test("a refusal with no words at all still names its status", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 502 })));
  expect(await postIntent(AGENT_ID, { verb: "apply" })).toEqual({
    applied: false,
    message: "Error 502 — try again.",
  });
});

test("the lane's own answer is read as the marked body it is", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ applied: false, message: "No model named 'gpt-9'." }), {
          status: 200,
        }),
    ),
  );
  const outcome = await postIntent(AGENT_ID, { verb: "apply" });
  expect(outcome.applied).toBe(false);
  expect(outcome.message).toBe("No model named 'gpt-9'.");
});
