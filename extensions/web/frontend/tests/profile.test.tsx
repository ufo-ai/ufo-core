import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";

import { AGENT, MEMBER, json, useStreamFake, wire } from "./harness";

const SET_PHOTO = {
  name: "set_member_photo",
  description: "Set the speaking member's own profile photo.",
  input_schema: { properties: { image: { type: "string", title: "Image" } }, required: ["image"] },
  call: { kind: "member_profile", action: "set_member_photo", input: {} },
  label: "Set photo",
};

const CLEAR_PHOTO = {
  name: "clear_member_photo",
  description: "Remove the speaking member's own profile photo.",
  input_schema: { properties: {} },
  call: { kind: "member_profile", action: "clear_member_photo", input: {} },
  label: "Remove photo",
};

const UNNAMED = {
  id: MEMBER.id,
  email: "rae.whitlock@example.com",
  admin: false,
  name: null,
  drawn_name: "rae.whitlock",
  photo_url: null,
  actions: [SET_PHOTO, CLEAR_PHOTO],
};

const DRAWN = {
  ...UNNAMED,
  name: "Rae Whitlock",
  drawn_name: "Rae Whitlock",
  photo_url: "members/m1/photo?v=abc123",
};

/** jsdom decodes no image, so Radix never swaps the fallback out — the picture is asserted by the
 *  source the circle asked its loader for, which is what `AvatarStack`'s own test reads. */
function pictures(): string[] {
  const asked: string[] = [];
  vi.stubGlobal(
    "Image",
    class {
      complete = false;
      naturalWidth = 0;
      addEventListener() {}
      removeEventListener() {}
      set src(url: string) {
        asked.push(url);
      }
    },
  );
  return asked;
}

beforeEach(() => {
  location.hash = "#/workspace/profile";
  useStreamFake();
});

test("an unnamed member is drawn under the local part of their address", async () => {
  wire({ "/workspace/profile": () => json(UNNAMED) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("rae.whitlock")).toBeTruthy();
  const main = within(screen.getByRole("main"));
  expect(main.getByText("rae.whitlock@example.com")).toBeTruthy();
  expect(main.getByText("Member")).toBeTruthy();
  expect(main.getByText("R")).toBeTruthy();
  expect(main.getByRole("button", { name: "Add" })).toBeTruthy();
  expect(main.queryByRole("button", { name: "Remove" })).toBeNull();
});

test("a name and photo already filled in are what the screen draws", async () => {
  const asked = pictures();
  wire({ "/workspace/profile": () => json(DRAWN) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("Rae Whitlock")).toBeTruthy();
  const main = within(screen.getByRole("main"));
  expect(main.getByRole("button", { name: "Change" })).toBeTruthy();
  await waitFor(() =>
    expect(asked).toContain("/surface/web/members/m1/photo?v=abc123"),
  );
});

test("naming yourself rides the profile intent lane and re-reads the screen", async () => {
  const posted: { url: string; body: unknown }[] = [];
  let payload: Record<string, unknown> = UNNAMED;
  wire({
    "/workspace/profile": () => json(payload),
    "/intents": (url, init) => {
      posted.push({ url, body: JSON.parse(String(init?.body)) });
      payload = { ...UNNAMED, name: "Rae W.", drawn_name: "Rae W." };
      return json({ applied: true, message: "" });
    },
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Edit name" }));
  await userEvent.type(screen.getByRole("textbox", { name: "Name" }), "Rae W.");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toEqual({
    url: "/surface/web/agents/" + AGENT.id + "/intents",
    body: {
      verb: "apply",
      kind: "member_profile",
      name: MEMBER.id,
      spec: { name: "Rae W." },
    },
  });
  expect(await screen.findByText("Name saved.")).toBeTruthy();
  expect(within(await screen.findByRole("main")).getByText("Rae W.")).toBeTruthy();
});

test("clearing the name sends null rather than an empty string", async () => {
  const posted: unknown[] = [];
  wire({
    "/workspace/profile": () => json(DRAWN),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "" });
    },
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Edit name" }));
  await userEvent.clear(screen.getByRole("textbox", { name: "Name" }));
  await userEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ spec: { name: null } });
});

test("removing a photo rides the kind's own action", async () => {
  const posted: { url: string; body: unknown }[] = [];
  wire({
    "/workspace/profile": () => json(DRAWN),
    "/clear_member_photo": (url, init) => {
      posted.push({ url, body: JSON.parse(String(init?.body)) });
      return json({ applied: true, message: "" });
    },
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Remove" }));
  await userEvent.click(await screen.findByRole("button", { name: "Confirm remove" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toEqual({
    url:
      "/surface/web/agents/" + AGENT.id + "/actions/member_profile/clear_member_photo",
    body: {},
  });
  expect(await screen.findByText("Photo removed.")).toBeTruthy();
});

test("a refused intent states the refusal and leaves the screen as it was", async () => {
  wire({
    "/workspace/profile": () => json(UNNAMED),
    "/intents": () => json({ applied: false, message: "That name is too long." }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Edit name" }));
  await userEvent.type(screen.getByRole("textbox", { name: "Name" }), "Rae");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));

  expect(await screen.findByText("That name is too long.")).toBeTruthy();
  expect(screen.getByRole("textbox", { name: "Name" })).toBeTruthy();
});
