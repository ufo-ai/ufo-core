import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { Dialog } from "@/components/ui/dialog";
import { RebuildDialog } from "@/kernel/rebuild";
import { MainAgentProvider } from "@/lib/mainAgent";

import { AGENT, AGENT_ID, json, wire } from "./harness";

const REBUILD = {
  name: "rebuild_report_digest",
  description: "Write the last seven days' entries again.",
  input_schema: { properties: {} },
  call: { kind: "report", action: "rebuild_report_digest", input: {} },
  label: "Rebuild entries",
};

const LANE = "/surface/web/agents/" + AGENT_ID + "/actions/report/rebuild_report_digest";

function open(outcome: { applied: boolean; message: string }) {
  const posted: { url: string; body: unknown }[] = [];
  const wired = wire({
    "/rebuild_report_digest": (url, init) => {
      posted.push({ url, body: JSON.parse(String(init?.body)) });
      return json(outcome);
    },
    "/actions/report$": () => json({ actions: [REBUILD] }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <Dialog open>
        <RebuildDialog title="Rebuild the radar" kind="report">
          <p>The entries are written again within minutes.</p>
        </RebuildDialog>
      </Dialog>
    </MainAgentProvider>,
  );
  return { posted, calls: wired.calls };
}

test("the rebuild is projected from the kind's declaration and posts on the action's route", async () => {
  const { posted, calls } = open({
    applied: true,
    message: "The last seven days' entries are written again.",
  });

  await userEvent.click(await screen.findByRole("button", { name: "Rebuild entries" }));

  await waitFor(() => expect(posted).toEqual([{ url: LANE, body: {} }]));
  expect(calls).toContain("/surface/web/actions/report");
  expect(await screen.findByText("The last seven days' entries are written again.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Rebuild entries" })).toBeNull();
  expect(screen.getByRole("button", { name: "Close" })).toBeTruthy();
});

test("a refused rebuild states the action's own words and keeps the act", async () => {
  const { posted } = open({
    applied: false,
    message: "Only a workspace admin rebuilds the radar.",
  });

  await userEvent.click(await screen.findByRole("button", { name: "Rebuild entries" }));

  await waitFor(() => expect(posted).toHaveLength(1));
  expect(await screen.findByText("Only a workspace admin rebuilds the radar.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Rebuild entries" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
});
