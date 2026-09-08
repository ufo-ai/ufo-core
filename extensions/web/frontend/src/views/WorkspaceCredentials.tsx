import { IconCheck } from "@tabler/icons-react";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

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
import { Sheet } from "@/components/ui/sheet";
import { Field, Input } from "@/components/ui/field";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { codeSpans } from "@/kernel/cards";
import {
  OutcomeNotice,
  outcomeNotice,
  type NoticeState,
  QUIET,
  Section,
} from "@/kernel/panel";
import type { ListingSpec } from "@/kernel/listing";
import { BASE, postIntent } from "@/lib/api";
import { useMainAgent } from "@/lib/mainAgent";
import { BrandMark, BRAND_MARKS } from "@/lib/brandMark";
import { PROVIDER_GLYPHS } from "@/lib/providerGlyph";
import type { ActionInput, ActionView, CredentialPrompt } from "@/lib/types";
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
  host: string;
  env: string;
  header: string;
};

const WORKSPACE_EXTENSION = "workspace_credentials";
const SLOT_KIND = "credential_slot";
const DEFAULT_HEADER = "Authorization";

const WS_PLACEHOLDER = "add-workspace-key";
const SVC_PLACEHOLDER = "add-service-key";

type PlaceholderRow = Slot & { placeholder: typeof WS_PLACEHOLDER | typeof SVC_PLACEHOLDER };

function isPlaceholder(row: Slot): row is PlaceholderRow {
  return (row as PlaceholderRow).placeholder !== undefined;
}

type CredentialsPayload = { slots: Slot[]; actions: ActionView[] };

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

const EXTENSION_SECTIONS: Record<string, string> = {
  workspace_credentials: "Workspace keys",
  browser_use: "Service keys",
  browserbase: "Service keys",
  perplexity: "Service keys",
  keyed_connectors: "Service keys",
  mcp: "MCP",
  turbopuffer: "Service keys",
};

function credentialSection(row: Slot) {
  if (isPlaceholder(row))
    return row.placeholder === WS_PLACEHOLDER ? "Workspace keys" : "Service keys";
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

/** The longest provider the portal draws that the slot's own name starts with. Read off the two sets
 *  that answer for a mark, not a table of endings: a table read `SLACK_BOT_TOKEN` as `slack_bot`. */
const SLOT_PROVIDERS = [...BRAND_MARKS, ...Object.keys(PROVIDER_GLYPHS)].sort(
  (left, right) => right.length - left.length,
);

function slotProvider(slot: string) {
  const name = slot.toLowerCase();
  return (
    SLOT_PROVIDERS.find((provider) => name === provider || name.startsWith(provider + "_")) ?? name
  );
}

function askedProvider(prompts: CredentialPrompt[]) {
  const asked = new Set(prompts.map((prompt) => slotProvider(prompt.slot)));
  return asked.size === 1 ? [...asked][0] : null;
}

/** Derived here the same way the `credential_slot` kind derives it, and the same name the `credential`
 *  kind gives the slot, so the row the panel writes is the row it reads back. */
function slotName(slot: string) {
  return slot
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

let heldServiceSlots: Slot[] = [];

export const CREDENTIALS: ListingSpec<CredentialsPayload, Slot> = {
  read: "/workspace/credentials",
  note: "Credential values are shared across the workspace.",
  lead: (
    <Section
      title="Coding providers"
      note="This account is yours alone. Each member connects their own, and it is used only for coding tasks."
    >
      <ConnectAccount />
    </Section>
  ),
  group: credentialSection,
  rows: (payload) => {
    heldServiceSlots = payload.slots.filter(
      (slot) =>
        !slot.filled &&
        credentialSection(slot) !== "Workspace keys" &&
        slot.slot !== MCP_SERVERS_SLOT,
    );
    const rows: Slot[] = payload.slots.filter(
      (slot) => slot.filled || slot.extension === WORKSPACE_EXTENSION,
    );
    {
      rows.push({
        name: WS_PLACEHOLDER,
        slot: WS_PLACEHOLDER,
        description: "Declare a key of the workspace's own.",
        extension: WORKSPACE_EXTENSION,
        filled: false,
        host: "",
        env: "",
        header: DEFAULT_HEADER,
        placeholder: WS_PLACEHOLDER,
      } as PlaceholderRow);
    }
    const unsetService = heldServiceSlots.filter(
      (slot) => slot.slot !== MCP_SERVERS_SLOT,
    );
    if (unsetService.length) {
      rows.push({
        name: SVC_PLACEHOLDER,
        slot: SVC_PLACEHOLDER,
        description: "Fill one of the service keys waiting for a value.",
        extension: "service_keys",
        filled: false,
        host: "",
        env: "",
        header: DEFAULT_HEADER,
        placeholder: SVC_PLACEHOLDER,
      } as PlaceholderRow);
    }
    return rows;
  },
  rowKey: (row) => row.name,
  search: (row) => [row.slot, row.description, row.extension, row.host, row.env].join(" "),
  list: {
    mark: (row) =>
      isPlaceholder(row) ? null : (
        <MarkTile>
          <BrandMark provider={slotProvider(row.slot)} className="text-ink" />
        </MarkTile>
      ),
    primary: { field: "slot" },
    meta: [
      {
        field: "description",
        render: (description, row) =>
          isPlaceholder(row) ? <span className="text-ink-quiet">{description}</span> : codeSpans(description),
      },
      { field: "host", render: (host, row) => (host ? codeSpans(`${row.env} → ${host}`) : null) },
    ],
  },

  empty: "No credential is set.",
  views: (payload) => payload.actions,
  actions: (row, { act, action, busy, actions }) => {
    const request = actions.find((view) => view.name === "request_credentials");
    if (isPlaceholder(row)) {
      return (
        <div className={ACTS}>
          {row.placeholder === WS_PLACEHOLDER ? (
            <DeclareCredential busy={busy} onDeclared={act} act={action} actions={actions} />
          ) : null}
          {row.placeholder === SVC_PLACEHOLDER && request ? (
            <SetCredential
              slots={heldServiceSlots}
              busy={busy}
              onPicked={(picked) => action(request, credentialRequest(picked))}
            />
          ) : null}
        </div>
      );
    }
    return (
      <div className={ACTS}>
        {row.filled ? (
          <span data-part="status" className="flex items-center gap-xs text-label text-ink-soft">
            <IconCheck role="img" aria-label={row.slot + " filled"} className="size-icon" />
            Filled
          </span>
        ) : (
          <span data-part="status" className="text-label text-ink-soft">
            No value
          </span>
        )}
        {row.extension === WORKSPACE_EXTENSION ? (
          <DeclareCredential
            busy={busy}
            onDeclared={act}
            act={action}
            actions={actions}
            slot={row}
          />
        ) : null}
        {request ? (
          <Button
            variant="row"
            disabled={busy}
            onClick={() => action(request, credentialRequest(row))}
          >
            {row.slot === MCP_SERVERS_SLOT ? "Update" : row.filled ? "Replace" : "Set"}
          </Button>
        ) : null}
        <ConfirmButton
          verb={row.extension === WORKSPACE_EXTENSION ? "Remove" : "Clear"}
          variant="row"
          disabled={busy}
          onClick={() =>
            act({
              verb: "delete",
              kind: row.extension === WORKSPACE_EXTENSION ? SLOT_KIND : "credential",
              name: row.name,
            })
          }
        />
      </div>
    );
  },
  credentials: (request, onStored, close) => (
    <Dialog open onOpenChange={(next) => (next ? undefined : close())}>
      <DialogContent>
        <PromptHeader provider={askedProvider(request.prompts)}>
          <DialogTitle>
            {request.prompts.length === 1 && request.prompts[0].slot === MCP_SERVERS_SLOT
              ? "Save MCP Server"
              : request.prompts.length === 1
                ? "Add service key"
                : "Add service keys"}
          </DialogTitle>
          <DialogDescription>
            {request.prompts.length === 1 && request.prompts[0].slot === MCP_SERVERS_SLOT
              ? "Add or update one server. Saved servers stay in place."
              : request.reason}
          </DialogDescription>
        </PromptHeader>
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

function PromptHeader({ provider, children }: { provider: string | null; children: ReactNode }) {
  if (provider === null) return <DialogHeader>{children}</DialogHeader>;
  return (
    <DialogHeader className="flex-row items-center gap-lg">
      <MarkTile>
        <BrandMark provider={provider} className="text-ink" />
      </MarkTile>
      <div className="flex min-w-0 flex-col gap-2xs">{children}</div>
    </DialogHeader>
  );
}

/** The value is collected at create time: saving runs the credential collection's `request_credentials`
 *  for the new slot, so one save lands the declaration and the value together under its sealed handoff. */
function DeclareCredential({
  busy,
  onDeclared,
  act,
  actions,
  slot,
}: {
  busy: boolean;
  onDeclared: (envelope: unknown) => void;
  act: (view: ActionView, input: ActionInput) => void;
  actions: ActionView[];
  slot?: Slot;
}) {
  const mainAgent = useMainAgent();
  const [open, setOpen] = useState(false);
  const [env, setEnv] = useState(slot?.env ?? "");
  const [host, setHost] = useState(slot?.host ?? "");
  const [header, setHeader] = useState(slot?.header ?? DEFAULT_HEADER);
  const [description, setDescription] = useState(slot?.description ?? "");
  const [value, setValue] = useState("");
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [saving, setSaving] = useState(false);
  const ready = Boolean(env.trim() && host.trim());

  async function declare() {
    if (!mainAgent || saving) return;
    const envelope = {
      verb: "apply",
      kind: SLOT_KIND,
      name: slotName(env),
      spec: {
        slot: env.trim().toLowerCase(),
        env: env.trim(),
        host: host.trim(),
        header: header.trim() || DEFAULT_HEADER,
        description: description.trim(),
      },
    };
    setSaving(true);
    const outcome = await postIntent(mainAgent.id, envelope);
    if (!outcome.applied) {
      setSaving(false);
      setNotice(outcomeNotice(outcome));
      return;
    }
    setOpen(false);
    setSaving(false);
    if (slot) {
      onDeclared(envelope);
      return;
    }
    const request = actions.find((view) => view.name === "request_credentials");
    if (!request) {
      onDeclared(envelope);
      return;
    }
    /* The value goes through the sealed prompt rather than this form: the request opens the same
       sealed handoff a row's Replace opens. */
    act(request, credentialRequest({
      name: slotName(env),
      slot: env.trim().toLowerCase(),
      description: description.trim(),
      extension: WORKSPACE_EXTENSION,
      filled: false,
      host: host.trim(),
      env: env.trim(),
      header: header.trim() || DEFAULT_HEADER,
    }));
  }

  return (
    <>
      {slot ? (
        <Button variant="row" disabled={busy} onClick={() => setOpen(true)}>
          Edit
        </Button>
      ) : (
        <Button variant="outline" size="bar" disabled={busy} onClick={() => setOpen(true)}>
          Add workspace key
        </Button>
      )}
      <Sheet
        open={open}
        title={slot ? "Edit workspace key" : "Add workspace key"}
        onClose={() => setOpen(false)}
      >
        <OutcomeNotice state={notice} />
        <div className="flex flex-col gap-lg">
          <Field
            label="Variable"
            htmlFor="credential-env"
            description="What the sandbox exports, and the name the key is filed under."
          >
            <Input
              id="credential-env"
              autoComplete="off"
              placeholder="ACME_API_KEY"
              required
              disabled={slot !== undefined}
              value={env}
              onChange={(event) => setEnv(event.target.value)}
            />
          </Field>
          <Field label="Host" htmlFor="credential-host" description="Where the value is sent.">
            <Input
              id="credential-host"
              autoComplete="off"
              placeholder="api.acme.com"
              required
              value={host}
              onChange={(event) => setHost(event.target.value)}
            />
          </Field>
          <Field label="Header" htmlFor="credential-header" description="The header it rides in.">
            <Input
              id="credential-header"
              autoComplete="off"
              placeholder={DEFAULT_HEADER}
              value={header}
              onChange={(event) => setHeader(event.target.value)}
            />
          </Field>
          <Field label="Description" htmlFor="credential-description">
            <Input
              id="credential-description"
              autoComplete="off"
              placeholder="Acme API key (Settings → API)."
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </Field>
          {!slot ? (
            <Field
              label="Value"
              htmlFor="credential-value"
              description="Typed once and stored encrypted, never shown again. The sandbox holds a placeholder only; the proxy sends the real value to this host alone."
            >
              <Input
                id="credential-value"
                type="password"
                autoComplete="off"
                placeholder="Paste the key"
                value={value}
                onChange={(event) => setValue(event.target.value)}
              />
            </Field>
          ) : null}
        </div>
        <div className="flex justify-end">
          <Button variant="send" disabled={!ready || busy || saving} onClick={() => void declare()}>
            Save
          </Button>
        </div>
      </Sheet>
    </>
  );
}

function SetCredential({
  slots,
  busy,
  onPicked,
}: {
  slots: Slot[];
  busy: boolean;
  onPicked: (row: Slot) => void;
}) {
  const unsetServiceSlots = slots;
  const [asking, setAsking] = useState(false);
  const [picked, setPicked] = useState("");
  return (
    <div className="flex">
      <Button variant="outline" size="bar" onClick={() => setAsking(true)}>
        Add service key
      </Button>
      <Sheet open={asking} title="Add service key" onClose={() => setAsking(false)}>
        <Field label="Credential" htmlFor="credential-slot">
          <Select value={picked} onValueChange={setPicked}>
            <SelectTrigger id="credential-slot">
              <SelectValue placeholder="Choose a credential" />
            </SelectTrigger>
            <SelectContent>
              {unsetServiceSlots.map((slot) => (
                <SelectItem key={slot.slot} value={slot.slot}>
                  <span className="flex min-w-0 items-center gap-sm">
                    <BrandMark
                      provider={slotProvider(slot.slot)}
                      className="size-(--size-icon)"
                    />
                    <span className="min-w-0 truncate">{slot.slot}</span>
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <div className="flex justify-end">
          <Button
            variant="send"
            disabled={!picked || busy}
            onClick={() => {
              setAsking(false);
              const row = unsetServiceSlots.find((slot) => slot.slot === picked);
              if (row) onPicked(row);
            }}
          >
            Continue
          </Button>
        </div>
      </Sheet>
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
