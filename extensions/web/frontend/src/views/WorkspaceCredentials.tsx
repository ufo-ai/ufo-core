import { IconCheck } from "@tabler/icons-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { Button, ConfirmButton } from "@/components/ui/button";
import { ACTS } from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field } from "@/components/ui/field";
import { codeSpans } from "@/kernel/cards";
import { OutcomeNotice, type NoticeState, QUIET } from "@/kernel/panel";
import type { ListingSpec } from "@/kernel/listing";
import { BASE } from "@/lib/api";
import type { ActionView, CredentialPrompt } from "@/lib/types";
import { ConnectAccount } from "@/views/ConnectAccount";
import {
  CredentialValueFields,
  MCP_SERVERS_SLOT,
} from "@/views/CredentialPrompt";

type Slot = {
  name: string;
  slot: string;
  description: string;
  extension: string;
  filled: boolean;
};

type CredentialsPayload = { slots: Slot[]; actions: ActionView[] };

/** The prompt the credential collection's `request_credentials` mints for one slot, authored here
 *  from the slot the listing read: the action's own input, posted through the projected view. */
function credentialRequest(row: Slot) {
  return {
    reason:
      row.extension + " authenticates with this value; it is stored encrypted and never shown again.",
    prompts: [{ slot: row.slot, prompt: row.description || row.slot }],
  };
}

const MODEL_PROVIDER_SLOTS = [
  "anthropic_api_key",
  "openai_api_key",
  "bedrock_api_key",
  "openrouter_api_key",
] as const;

const SERVICE_KEY_SLOTS = [
  "datadog_api_key",
  "datadog_application_key",
  "datadog_api_host",
  "perplexity_api_key",
  "turbopuffer_api_key",
  "browserbase_api_key",
  "browser_use_api_key",
] as const;

const MCP_SLOTS = [MCP_SERVERS_SLOT] as const;

const SECTION_ORDER = ["Model providers", "Service keys", "MCP"];
const EXTENSION_SECTIONS: Record<string, string> = {
  browser_use: "Service keys",
  browserbase: "Service keys",
  perplexity: "Service keys",
  keyed_connectors: "Service keys",
  mcp: "MCP",
  turbopuffer: "Service keys",
};

function credentialSection(row: Slot) {
  const slot = row.slot.toLowerCase();
  if (MODEL_PROVIDER_SLOTS.includes(slot as (typeof MODEL_PROVIDER_SLOTS)[number]))
    return "Model providers";
  if (SERVICE_KEY_SLOTS.includes(slot as (typeof SERVICE_KEY_SLOTS)[number]))
    return "Service keys";
  if (MCP_SLOTS.includes(slot as (typeof MCP_SLOTS)[number])) return "MCP";
  return EXTENSION_SECTIONS[row.extension] ?? extensionTitle(row.extension);
}

function extensionTitle(extension: string) {
  return extension
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export const CREDENTIALS: ListingSpec<CredentialsPayload, Slot> = {
  read: "/workspace/credentials",
  note: "Credential values are shared across the workspace.",
  /* The slots below are the workspace's; a coding account is the member's own, and this is where
     they come back to replace one that was rotated or revoked. */
  lead: (
    <div className="flex flex-col gap-sm px-lg pt-lg">
      <p className="m-0 text-ink-soft">
        This account is yours alone. Each member connects their own, and it is used only for coding
        tasks.
      </p>
      <ConnectAccount />
    </div>
  ),
  group: credentialSection,
  rows: (payload) =>
    [...payload.slots].sort((left, right) => {
      const leftRank = SECTION_ORDER.indexOf(credentialSection(left));
      const rightRank = SECTION_ORDER.indexOf(credentialSection(right));
      const normalizedLeftRank = leftRank === -1 ? SECTION_ORDER.length : leftRank;
      const normalizedRightRank = rightRank === -1 ? SECTION_ORDER.length : rightRank;
      if (normalizedLeftRank !== normalizedRightRank)
        return normalizedLeftRank - normalizedRightRank;
      const leftSection = credentialSection(left);
      const rightSection = credentialSection(right);
      if (leftSection !== rightSection) return leftSection.localeCompare(rightSection);
      if (left.filled !== right.filled) return left.filled ? -1 : 1;
      return left.slot.localeCompare(right.slot);
    }),
  rowKey: (row) => row.name,
  search: (row) => [row.slot, row.description, row.extension].join(" "),
  chips: [
    { label: "Filled", has: (row) => row.filled },
    { label: "Not set", has: (row) => !row.filled },
  ],
  /* A slot has no screen of its own: the row is the whole record, and the description is what says
     which value belongs in it. */
  list: {
    primary: { field: "slot" },
    meta: [{ field: "description", render: (description) => codeSpans(description) }],
    whole: true,
  },
  empty: "No credential slots are declared.",
  views: (payload) => payload.actions,
  actions: (row, { act, action, busy, actions }) => {
    const request = actions.find((view) => view.name === "request_credentials");
    return (
      <div className={ACTS}>
        {row.filled ? (
          <span
            data-part="status"
            className="flex items-center gap-xs text-label text-ink-soft"
          >
            <IconCheck role="img" aria-label={row.slot + " filled"} className="size-icon" />
            Filled
          </span>
        ) : null}
        {request ? (
          <Button
            variant="row"
            disabled={busy}
            onClick={() => action(request, credentialRequest(row))}
          >
            {row.filled ? (row.slot === MCP_SERVERS_SLOT ? "Update" : "Replace") : "Set"}
          </Button>
        ) : null}
        {row.filled ? (
          <ConfirmButton
            verb="Clear"
            variant="row"
            disabled={busy}
            onClick={() =>
              act({ verb: "delete", kind: "credential", name: row.name })
            }
          />
        ) : null}
      </div>
    );
  },
  credentials: (request, onStored, close) => (
    <Dialog open onOpenChange={(next) => (next ? undefined : close())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {request.prompts.length === 1 && request.prompts[0].slot === MCP_SERVERS_SLOT
              ? "Save MCP Server"
              : request.prompts.length === 1
                ? "Set Credential"
                : "Set Credentials"}
          </DialogTitle>
          <DialogDescription>
            {request.prompts.length === 1 && request.prompts[0].slot === MCP_SERVERS_SLOT
              ? "Add or update one server. Saved servers stay in place."
              : request.reason}
          </DialogDescription>
        </DialogHeader>
        <CredentialPromptDialogForm
          sealed={request.sealed}
          prompts={request.prompts}
          onStored={onStored}
          onComplete={close}
        />
      </DialogContent>
    </Dialog>
  ),
};

function CredentialPromptDialogForm({
  sealed,
  prompts,
  onStored,
  onComplete,
}: {
  sealed: string;
  prompts: CredentialPrompt[];
  onStored: (slots: string[]) => void;
  onComplete: () => void;
}) {
  const [pending, setPending] = useState(prompts);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const held = useRef<string[]>([]);
  const reported = useRef(false);
  const ready = pending.every((prompt) => values[prompt.slot]?.trim());

  /** Reporting a stored slot re-reads the listing, which takes this dialog down with it — so a
   *  request whose slots did not all land holds what did until the member is finished with it,
   *  and reports on the way out however the dialog closes. */
  function report() {
    if (reported.current || !held.current.length) return;
    reported.current = true;
    onStored(held.current);
  }

  useEffect(() => () => report(), []);

  async function store(prompt: CredentialPrompt, value = values[prompt.slot]): Promise<string | null> {
    const body = new URLSearchParams({
      sealed,
      slot: prompt.slot,
      value,
    });
    try {
      const res = await fetch(BASE + "/credentials", {
        method: "POST",
        body,
        credentials: "same-origin",
      });
      return res.ok ? null : (await res.text()) || "Unable to store credential.";
    } catch {
      return "Network error — try again.";
    }
  }

  async function remove(prompt: CredentialPrompt, name: string) {
    if (busy) return;
    setBusy(true);
    const failure = await store(prompt, JSON.stringify({ name, remove: true }));
    setBusy(false);
    if (failure !== null) {
      setNotice({ text: failure, refused: true });
      return;
    }
    held.current.push(prompt.slot);
    report();
    onComplete();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !ready) return;
    setBusy(true);
    const refused: CredentialPrompt[] = [];
    let message = "";
    for (const prompt of pending) {
      const failure = await store(prompt);
      if (failure === null) {
        held.current.push(prompt.slot);
        continue;
      }
      refused.push(prompt);
      message = message || failure;
    }
    setBusy(false);
    if (refused.length) {
      setPending(refused);
      setNotice({ text: message, refused: true });
      return;
    }
    report();
    onComplete();
  }

  return (
    <>
      <OutcomeNotice state={notice} />
      <form
        id="set-credentials"
        onSubmit={submit}
        className="flex flex-col gap-lg"
      >
        {pending.map((prompt) =>
          prompt.slot === MCP_SERVERS_SLOT ? (
            <CredentialValueFields
              key={prompt.slot}
              idPrefix={prompt.slot}
              prompt={prompt}
              onChange={(value) =>
                setValues((current) => ({ ...current, [prompt.slot]: value }))
              }
              onRemove={
                pending.length === 1 ? (name) => void remove(prompt, name) : undefined
              }
              busy={busy}
            />
          ) : (
            <Field key={prompt.slot} label={prompt.prompt} htmlFor={prompt.slot}>
              <CredentialValueFields
                idPrefix={prompt.slot}
                prompt={prompt}
                onChange={(value) =>
                  setValues((current) => ({ ...current, [prompt.slot]: value }))
                }
              />
            </Field>
          ),
        )}
      </form>
      <DialogFooter>
        <Button
          type="submit"
          form="set-credentials"
          variant="send"
          busy={busy}
          disabled={!ready}
        >
          {pending.length === 1 && pending[0].slot === MCP_SERVERS_SLOT
            ? "Save server"
            : pending.length === 1
              ? "Set credential"
              : "Set credentials"}
        </Button>
      </DialogFooter>
    </>
  );
}
