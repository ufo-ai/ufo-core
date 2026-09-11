import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { MainAgentProvider } from "@/lib/mainAgent";
import { clockOf, localMoment } from "@/lib/moments";
import { Radar } from "@/views/Radar";

import { AGENT, RADAR_TOUR, json, wire } from "./harness";

function pending() {
  wire({ "/objects/report": () => new Promise<Response>(() => {}) });
  return render(
    <MainAgentProvider agents={[AGENT]}>
      <Radar place={{}} onPlace={() => {}} />
    </MainAgentProvider>,
  );
}

test("the feed waits as the rows it will draw rather than as a grid of cards", async () => {
  const { container } = pending();

  await screen.findByRole("heading", { name: "Radar" });
  const rows = container.querySelectorAll("ol > li");
  expect(rows.length).toBeGreaterThan(0);
  for (const row of rows) {
    expect(row.className).toContain("border-b border-edge");
    const line = row.firstElementChild!;
    expect(line.className).toContain("flex items-start gap-2xl px-sm py-2xl");
    expect(line.querySelectorAll('[data-part="skeleton"]').length).toBe(5);
  }
  expect(container.querySelector(".grid")).toBeNull();
});

test("the tour stands under the feed's own band where no run has reported", async () => {
  wire({
    "/objects/report": () => json({ objects: [] }),
    "/actions/report$": () => json({ actions: [] }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <Radar place={{}} onPlace={() => {}} />
    </MainAgentProvider>,
  );

  expect(await screen.findByRole("heading", { name: "Radar" })).toBeTruthy();
  expect(await screen.findByRole("heading", { name: RADAR_TOUR })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Rebuild entries" })).toBeNull();
});

test("a run fired on an earlier day states its clock in the reader's own zone", async () => {
  const fired = new Date(2026, 6, 30, 9, 0, 0);
  wire({
    "/objects/report": () =>
      new Response(
        JSON.stringify({
          objects: [
            {
              name: "11111111-1111-4111-8111-111111111111",
              agent_id: AGENT.id,
              conversation: "22222222-2222-4222-8222-222222222222",
              fired_at: fired.toISOString(),
              status: "done",
              task: null,
              surface: "web",
              source: null,
              text: "The nightly ran.",
              entry: null,
              artifacts: [],
            },
          ],
          next_cursor: null,
        }),
        { headers: { "content-type": "application/json" } },
      ),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <Radar place={{}} onPlace={() => {}} />
    </MainAgentProvider>,
  );

  expect(await screen.findByText(clockOf(localMoment(fired)))).toBeTruthy();
  const [year, month, date] = localMoment(fired).slice(0, 10).split("-");
  const heading = MONTHS[Number(month) - 1] + " " + Number(date) + ", " + year;
  expect(screen.getAllByRole("heading").map((h) => h.textContent)).toContain(heading);
});

const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];
