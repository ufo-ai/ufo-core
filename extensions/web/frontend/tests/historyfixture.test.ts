import { expect, test } from "vitest";

import {
  HISTORY_FIXTURE_AGENT_ID,
  HISTORY_FIXTURE_ROW_COUNT,
  historyFixturePayload,
} from "../history-fixture";

test("the history fixture opens one chat app with more than twenty-five conversations", () => {
  const boot = historyFixturePayload("/surface/web/api/agents") as {
    agents: { id: string; app: string }[];
  };
  const history = historyFixturePayload("/surface/web/objects/conversation") as {
    objects: { agent_id: string; title: string }[];
  };

  expect(boot.agents).toEqual([
    expect.objectContaining({ id: HISTORY_FIXTURE_AGENT_ID, app: "chat" }),
  ]);
  expect(history.objects).toHaveLength(HISTORY_FIXTURE_ROW_COUNT);
  expect(HISTORY_FIXTURE_ROW_COUNT).toBeGreaterThan(25);
  expect(history.objects[0]).toMatchObject({
    agent_id: HISTORY_FIXTURE_AGENT_ID,
    title: "History row 01",
  });
});
