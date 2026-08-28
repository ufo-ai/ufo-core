import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import { ActionForm } from "@/kernel/action";
import type { IntentOutcome } from "@/lib/api";
import type { ActionView } from "@/lib/types";

import { refusedNotice } from "./harness";

const SAVED: IntentOutcome = { applied: true, message: "Saved." };

const ADD_MEMBER: ActionView = {
  name: "add_member",
  description: "Add a member to this workspace ahead of their first contact.",
  input_schema: {
    properties: {
      email: { type: "string", format: "email", title: "Email" },
      admin: { type: "boolean", title: "Admin", default: false },
      notify: { type: "boolean", title: "Notify", default: true },
    },
    required: ["email"],
  },
  call: { kind: "member", action: "add_member", input: {} },
  label: "Add member",
};

const MANAGE_BILLING: ActionView = {
  name: "manage_billing",
  description: "Read or change how this workspace pays.",
  input_schema: {
    properties: {
      action: { type: "string", title: "Action", enum: ["status", "portal", "autopay"] },
      autopay_dollars: {
        title: "Autopay Dollars",
        anyOf: [{ type: "integer" }, { type: "null" }],
        default: null,
      },
      autopay_below_dollars: {
        title: "Autopay Below Dollars",
        anyOf: [{ type: "integer" }, { type: "null" }],
        default: null,
      },
    },
    required: ["action"],
  },
  call: { kind: "workspace", name: "main", action: "manage_billing", input: {} },
  label: "Manage billing",
};

const GRANT_WEB_ACCESS: ActionView = {
  name: "grant_web_access",
  description: "Let this member reach the agent on the web.",
  input_schema: { properties: {} },
  call: { kind: "member", name: "person@work.com", action: "grant_web_access", input: {} },
  label: "Grant web access",
};

const REVOKE_WEB_ACCESS: ActionView = {
  ...GRANT_WEB_ACCESS,
  name: "revoke_web_access",
  description: "End this member's web access.",
  call: { ...GRANT_WEB_ACCESS.call, action: "revoke_web_access" },
  label: "Revoke web access",
  confirm: "Their portal session ends at once.",
};

const RESTORE_APPLICATION: ActionView = {
  name: "restore_application",
  description: "Return an archived app to the workspace.",
  input_schema: {
    properties: { name: { type: "string", title: "Name", maxLength: 64 } },
    required: ["name"],
  },
  call: { kind: "agent", name: "~archived-1234", action: "restore_application", input: {} },
  label: "Restore app",
  confirm: "The app returns to the workspace under this name.",
};

function acting(outcome: IntentOutcome = SAVED) {
  return vi.fn(async () => outcome);
}

test("an action's fields are drawn from its schema with defaults standing", () => {
  render(<ActionForm view={ADD_MEMBER} act={acting()} />);

  expect(screen.getByRole("form", { name: "Add member" })).toBeTruthy();
  const email = screen.getByRole("textbox", { name: "Email" }) as HTMLInputElement;
  expect(email.type).toBe("email");
  expect(email.required).toBe(true);
  expect((screen.getByRole("switch", { name: "Admin" }) as HTMLInputElement).checked).toBe(false);
  expect((screen.getByRole("switch", { name: "Notify" }) as HTMLInputElement).checked).toBe(true);
  expect(screen.getByRole("button", { name: "Add member" }).hasAttribute("disabled")).toBe(true);
});

test("a filled form submits the action's input body alone, defaults included", async () => {
  const act = acting();
  render(<ActionForm view={ADD_MEMBER} act={act} />);

  await userEvent.type(screen.getByRole("textbox", { name: "Email" }), "person@work.com");
  await userEvent.click(screen.getByRole("switch", { name: "Admin" }));
  await userEvent.click(screen.getByRole("button", { name: "Add member" }));

  expect(act).toHaveBeenCalledOnce();
  expect(act).toHaveBeenCalledWith({ email: "person@work.com", admin: true, notify: true });
  const said = await screen.findByText("Saved.");
  expect(said.className).not.toContain("bg-attention");
});

test("a refusal stands on the form in the refusal register", async () => {
  const act = acting({ applied: false, message: "Only a workspace admin can add members." });
  render(<ActionForm view={ADD_MEMBER} act={act} />);

  await userEvent.type(screen.getByRole("textbox", { name: "Email" }), "person@work.com");
  await userEvent.click(screen.getByRole("button", { name: "Add member" }));

  await refusedNotice("Only a workspace admin can add members.");
});

test("an enum is picked from its choices and an optional number left empty is omitted", async () => {
  const act = acting();
  render(<ActionForm view={MANAGE_BILLING} act={act} />);

  const submit = screen.getByRole("button", { name: "Manage billing" });
  expect(submit.hasAttribute("disabled")).toBe(true);

  await userEvent.click(screen.getByRole("combobox", { name: "Action" }));
  await userEvent.click(screen.getByRole("option", { name: "Portal" }));
  await userEvent.click(submit);

  expect(act).toHaveBeenCalledWith({ action: "portal" });
});

test("an integer field is typed on a number control and submits as a number", async () => {
  const act = acting();
  render(<ActionForm view={MANAGE_BILLING} act={act} />);

  await userEvent.click(screen.getByRole("combobox", { name: "Action" }));
  await userEvent.click(screen.getByRole("option", { name: "Autopay" }));
  await userEvent.type(screen.getByRole("spinbutton", { name: "Autopay Dollars" }), "25");
  await userEvent.type(screen.getByRole("spinbutton", { name: "Autopay Below Dollars" }), "10");
  await userEvent.click(screen.getByRole("button", { name: "Manage billing" }));

  expect(act).toHaveBeenCalledWith({
    action: "autopay",
    autopay_dollars: 25,
    autopay_below_dollars: 10,
  });
});

test("an action with no fields is the act alone and submits an empty body", async () => {
  const act = acting();
  render(<ActionForm view={GRANT_WEB_ACCESS} act={act} />);

  expect(screen.queryByRole("textbox")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Grant web access" }));

  expect(act).toHaveBeenCalledOnce();
  expect(act).toHaveBeenCalledWith({});
});

test("a confirming action arms on the first press and commits on the second", async () => {
  const act = acting();
  render(<ActionForm view={REVOKE_WEB_ACCESS} act={act} />);

  await userEvent.click(screen.getByRole("button", { name: "Revoke web access" }));
  expect(act).not.toHaveBeenCalled();
  expect(screen.getByText("Their portal session ends at once.")).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Confirm revoke web access" }));
  expect(act).toHaveBeenCalledOnce();
  expect(act).toHaveBeenCalledWith({});
});

test("leaving an armed action disarms it", async () => {
  const act = acting();
  render(<ActionForm view={REVOKE_WEB_ACCESS} act={act} />);

  await userEvent.click(screen.getByRole("button", { name: "Revoke web access" }));
  await userEvent.tab();

  expect(screen.getByRole("button", { name: "Revoke web access" })).toBeTruthy();
  expect(screen.queryByText("Their portal session ends at once.")).toBeNull();
  expect(act).not.toHaveBeenCalled();
});

test("a pinned field never draws and rides the body as given; a seeded field starts filled", async () => {
  const act = acting();
  render(
    <ActionForm
      view={MANAGE_BILLING}
      act={act}
      fixed={{ action: "autopay" }}
      initial={{ autopay_dollars: "25" }}
    />,
  );

  expect(screen.queryByRole("combobox", { name: "Action" })).toBeNull();
  const dollars = screen.getByRole("spinbutton", { name: "Autopay Dollars" }) as HTMLInputElement;
  expect(dollars.value).toBe("25");
  await userEvent.click(screen.getByRole("button", { name: "Manage billing" }));

  expect(act).toHaveBeenCalledWith({ action: "autopay", autopay_dollars: 25 });
});

test("editing a field disarms, and the commit carries what is in the fields", async () => {
  const act = acting();
  render(<ActionForm view={RESTORE_APPLICATION} act={act} />);

  await userEvent.type(screen.getByRole("textbox", { name: "Name" }), "radar");
  await userEvent.click(screen.getByRole("button", { name: "Restore app" }));
  expect(screen.getByRole("button", { name: "Confirm restore app" })).toBeTruthy();

  await userEvent.type(screen.getByRole("textbox", { name: "Name" }), "2");
  await userEvent.click(screen.getByRole("button", { name: "Restore app" }));
  expect(act).not.toHaveBeenCalled();

  await userEvent.click(screen.getByRole("button", { name: "Confirm restore app" }));
  expect(act).toHaveBeenCalledOnce();
  expect(act).toHaveBeenCalledWith({ name: "radar2" });
});
