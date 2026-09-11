import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { MainAgentProvider } from "@/lib/mainAgent";
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
