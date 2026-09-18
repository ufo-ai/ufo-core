import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { Me, WorkspaceId } from "@/lib/audience";
import { MainAgentProvider } from "@/lib/mainAgent";
import { ALL_SURFACES, SurfacesProvider } from "@/lib/surfaces";
import { WorkspacePane } from "@/views/routed";
import { AGENT, AGENT_ID, MEMBER, WORKSPACE_ID, json, wire, type Route } from "./harness";

const EXPORT = {
  confirm: "This archive includes all members' private conversations, files, and memory. Anyone with the archive can read this data.",
  name: "export", label: "Export workspace", description: "",
  input_schema: { properties: {} },
  call: { kind: "workspace", action: "export", input: {} },
};
const READY = { id: "export-1", status: "ready", created_at: "2026-09-17T00:00:00Z", finished_at: "2026-09-17T00:01:00Z", expires_at: "2026-09-18T00:01:00Z", size_bytes: 2048, error: null };
const LANE = "/surface/web/agents/" + AGENT_ID + "/actions/workspace/export";

function adminPage(admin: boolean, routes: Record<string, Route> = {}) {
  const wired = wire({ "/actions/workspace$": () => json({ actions: [EXPORT] }), "/surface/web/workspace/export$": () => json({ export: null }), ...routes });
  render(
    <Me.Provider value={{ ...MEMBER, admin }}>
      <WorkspaceId.Provider value={WORKSPACE_ID}>
        <SurfacesProvider surfaces={ALL_SURFACES}>
          <MainAgentProvider agents={[AGENT]}>
            <WorkspacePane view="admin" place={{}} crumb={undefined} />
          </MainAgentProvider>
        </SurfacesProvider>
      </WorkspaceId.Provider>
    </Me.Provider>,
  );
  return wired;
}

test("admin can export and download the archive", async () => {
  let requested = false;
  const wired = adminPage(true, {
    "/surface/web/workspace/export$": () => json({ export: requested ? READY : null }),
    [LANE]: () => { requested = true; return json({ applied: true, message: "Export requested." }); },
  });
  await userEvent.click(await screen.findByRole("button", { name: "Export workspace" }));
  await userEvent.click(await screen.findByRole("button", { name: "Export all member data" }));
  const link = await screen.findByRole("link", { name: "Download export" });
  expect(link.getAttribute("href")).toBe("/surface/web/workspace/export/export-1/download");
  const posted = wired.handler.mock.calls.find(([url]) => String(url).includes(LANE));
  expect(JSON.parse(String((posted?.[1] as RequestInit).body))).toEqual({});
});

test("non-admin cannot open Admin through a direct route", async () => {
  const wired = adminPage(false);
  await waitFor(() => expect(screen.queryByText("Workspace data")).toBeNull());
  expect(screen.queryByRole("button", { name: "Export workspace" })).toBeNull();
  expect(wired.calls.some((url) => url.includes("/actions/workspace"))).toBe(false);
});

test("export stays disabled while the archive is prepared", async () => {
  let finish: (response: Response) => void = () => {};
  adminPage(true, { [LANE]: () => new Promise<Response>((resolve) => { finish = resolve; }) });
  await userEvent.click(await screen.findByRole("button", { name: "Export workspace" }));
  await userEvent.click(await screen.findByRole("button", { name: "Export all member data" }));
  expect((await screen.findByRole("button", { name: "Requesting export" }) as HTMLButtonElement).disabled).toBe(true);
  finish(json({ applied: false, message: "Export exceeds the size limit." }));
  expect(await screen.findByText("Export exceeds the size limit.")).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Download export" })).toBeNull();
});

test("a deploy without export does not offer the button", async () => {
  adminPage(true, { "/actions/workspace$": () => json({ actions: [] }) });
  expect(await screen.findByText("Workspace export is not available.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Export workspace" })).toBeNull();
});


test("export warns about private data and cancellation does not submit", async () => {
  const wired = adminPage(true);
  await userEvent.click(await screen.findByRole("button", { name: "Export workspace" }));
  expect(await screen.findByRole("dialog", { name: "Export all member data?" })).toBeTruthy();
  expect(screen.getByText(EXPORT.confirm)).toBeTruthy();
  expect(wired.calls.some((url) => url.includes(LANE))).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(wired.calls.some((url) => url.includes(LANE))).toBe(false);
});


test("preparing export survives a page load and disables duplicate requests", async () => {
  adminPage(true, { "/surface/web/workspace/export$": () => json({ export: { ...READY, status: "preparing" } }) });
  expect(await screen.findByText("Preparing")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Export workspace" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByRole("link", { name: "Download export" })).toBeNull();
});

test("expired export has no download link", async () => {
  adminPage(true, { "/surface/web/workspace/export$": () => json({ export: { ...READY, status: "expired" } }) });
  expect(await screen.findByText("Expired")).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Download export" })).toBeNull();
});
