import { IconCheck } from "@tabler/icons-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { Button, ConfirmButton } from "@/components/ui/button";
import { MarkTile } from "@/components/ui/item";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { codeSpans } from "@/kernel/cards";
import { OutcomeNotice, type NoticeState, QUIET, Section } from "@/kernel/panel";
import type { ListingSpec } from "@/kernel/listing";
import { BASE } from "@/lib/api";
import { BrandMark } from "@/lib/brandMark";
import { ConnectAccount } from "@/views/ConnectAccount";
import {
  CredentialValueFields,
  MCP_SERVERS_SLOT,
} from "@/views/CredentialPrompt";
import type { CredentialPrompt } from "@/lib/types";

type Slot = {
  name: string;
  slot: string;
  description: string;
  extension: string;
  filled: boolean;
};

type CredentialsPayload = { slots: Slot[] };

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

/** The prompt the credential collection's `request_credentials` mints for one slot, authored here
 *  from the slot the listing read: the action's own input, posted through the projected view. */
function credentialRequest(row: Slot) {
  return {
    reason:
      row.extension + " authenticates with this value; it is stored encrypted and never shown again.",
    prompts: [{ slot: row.slot, prompt: row.description || row.slot }],
  };
}

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

/** The provider a slot's value authenticates with, off the slot's own name: the marks are keyed by
 *  provider, so `DATADOG_API_KEY` and `DATADOG_APPLICATION_KEY` stand under one logo and a provider
 *  nobody vendored a mark for takes its glyph. */
const SLOT_SUFFIXES = ["_api_key", "_application_key", "_api_host", "_servers", "_key", "_token"];

function slotProvider(slot: string) {
  const name = slot.toLowerCase();
  const suffix = SLOT_SUFFIXES.find((entry) => name.endsWith(entry));
  return suffix ? name.slice(0, -suffix.length) : name;
}

export const CREDENTIALS: ListingSpec<CredentialsPayload, Slot> = {
  read: "/workspace/credentials",
  note: "Credential values are shared across the workspace.",
  /* The coding providers stand whether or not a member holds one, above the slots and under a
     heading of their own: a slot is the workspace's, a coding account is the member's. */
  lead: (
    <Section
      title="Coding providers"
      note="This account is yours alone. Each member connects their own, and it is used only for coding tasks."
    >
      <ConnectAccount />
    </Section>
  ),
  group: credentialSection,
  /* The values the workspace holds, and no row for a slot nobody has filled: an unset slot is
     nothing to read and nothing to replace, and a member fills one by asking the agent for it. */
  rows: (payload) =>
    payload.slots
      .filter((slot) => slot.filled)
      .sort((left, right) => {
        const leftRank = SECTION_ORDER.indexOf(credentialSection(left));
        const rightRank = SECTION_ORDER.indexOf(credentialSection(right));
        const normalizedLeftRank = leftRank === -1 ? SECTION_ORDER.length : leftRank;
        const normalizedRightRank = rightRank === -1 ? SECTION_ORDER.length : rightRank;
        if (normalizedLeftRank !== normalizedRightRank)
          return normalizedLeftRank - normalizedRightRank;
        const leftSection = credentialSection(left);
        const rightSection = credentialSection(right);
        if (leftSection !== rightSection) return leftSection.localeCompare(rightSection);
        return left.slot.localeCompare(right.slot);
      }),
  rowKey: (row) => row.name,
  search: (row) => [row.slot, row.description, row.extension].join(" "),
  list: {
    mark: (row) => (
      <MarkTile>
        <BrandMark provider={slotProvider(row.slot)} className="text-ink" />
      </MarkTile>
    ),
    primary: { field: "slot" },
    meta: [{ field: "description", render: (description) => codeSpans(description) }],
  },
  empty: "No credential is set.",
  /* Every slot with a value has a row, and a slot with none has none — so the act that fills one
     stands here, for the member a surface sent to this screen to fill it. */
  offer: (payload, { kindAct, busy }) => {
    const unset = payload.slots.filter((slot) => !slot.filled);
    return unset.length ? (
      <SetCredential
        slots={unset}
        busy={busy}
        onPicked={(row) => kindAct("credential", "request_credentials", credentialRequest(row))}
      />
    ) : null;
  },
  actions: (row, { act, kindAct, busy }) => (
    <div className={ACTS}>
      <span data-part="status" className="flex items-center gap-xs text-label text-ink-soft">
        <IconCheck role="img" aria-label={row.slot + " filled"} className="size-icon" />
        Filled
      </span>
      <Button
        variant="row"
        disabled={busy}
        onClick={() => kindAct("credential", "request_credentials", credentialRequest(row))}
      >
        {row.slot === MCP_SERVERS_SLOT ? "Update" : "Replace"}
      </Button>
      <ConfirmButton
        verb="Clear"
        variant="row"
        disabled={busy}
        onClick={() => act({ verb: "delete", kind: "credential", name: row.name })}
      />
    </div>
  ),
  credentials: (request, onStored, close) => (
    <Dialog open onOpenChange={(next) => (next ? undefined : close())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {request.prompts.length === 1 ? "Set Credential" : "Set Credentials"}
          </DialogTitle>
          <DialogDescription>{request.reason}</DialogDescription>
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

const PICKED_SLOT = "credential-slot";

/** The act over the rows: the screen lists the values the workspace holds, so a slot with none is
 *  reached by naming it. Picking one raises the same prompt a row's Replace raises, and the value
 *  is typed there rather than here — the prompt is what carries the seal. */
function SetCredential({
  slots,
  busy,
  onPicked,
}: {
  slots: Slot[];
  busy: boolean;
  onPicked: (row: Slot) => void;
}) {
  const [asking, setAsking] = useState(false);
  const [picked, setPicked] = useState("");
  const row = slots.find((slot) => slot.slot === picked);
  return (
    <div className="flex">
      <Button variant="outline" size="bar" onClick={() => setAsking(true)}>
        Set a credential
      </Button>
      <Dialog open={asking} onOpenChange={setAsking}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Set Credential</DialogTitle>
            <DialogDescription>
              Name the slot to fill. Its value is stored encrypted and never shown again.
            </DialogDescription>
          </DialogHeader>
          <Field label="Credential" htmlFor={PICKED_SLOT} description={row?.description}>
            <Select value={picked} onValueChange={setPicked}>
              <SelectTrigger id={PICKED_SLOT}>
                <SelectValue placeholder="Choose a credential" />
              </SelectTrigger>
              <SelectContent>
                {slots.map((slot) => (
                  <SelectItem key={slot.slot} value={slot.slot}>
                    {slot.slot}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <DialogFooter>
            <Button
              variant="send"
              disabled={row === undefined || busy}
              onClick={() => {
                setAsking(false);
                if (row) onPicked(row);
              }}
            >
              Continue
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

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
