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
import { Listing, type ListingSpec, type RowContext } from "@/kernel/listing";
import type { Placement } from "@/kernel/pager";
import { BASE, postAction } from "@/lib/api";
import { useMainAgent } from "@/lib/mainAgent";
import { BrandMark, BRAND_MARKS } from "@/lib/brandMark";
import { PROVIDER_GLYPHS } from "@/lib/providerGlyph";
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
  host: string;
  env: string;
  header: string;
};

const WORKSPACE_EXTENSION = "workspace_credentials";
const SLOT_KIND = "credential_slot";
const DEFAULT_HEADER = "Authorization";

/* The shapes `ufo_ext_workspace_credentials` refuses a declaration for — held here too, so the
   form says no before the round-trip rather than after it. */
const VARIABLE_PATTERN = "[A-Z][A-Z0-9_]{2,63}";
const HOST_PATTERN = "[A-Za-z0-9-]+(\\.[A-Za-z0-9-]+)+";
const HEADER_PATTERN = "[A-Za-z][A-Za-z0-9-]{0,63}";

type CredentialsPayload = { slots: Slot[]; actions: ActionView[] };

function credentialRequest(row: Slot) {
  return {
    reason:
      (row.host ? "Sent to " + row.host + " and nowhere else. " : "") +
      "Stored encrypted and never shown again.",
    prompts: [{ slot: row.slot, prompt: row.description || row.env || row.slot }],
  };
}

const MODEL_PROVIDER_SLOTS = [
  "anthropic_api_key",
  "openai_api_key",
  "bedrock_api_key",
  "openrouter_api_key",
] as const;

const SECTION_ORDER = ["Model providers", "Service keys", "Workspace keys", "MCP"];

/* A closed set of four. A section per extension gave Slack a heading and one row under it, and
   every extension that declares a key would earn one the same way. */
function credentialSection(row: Slot) {
  if (row.extension === WORKSPACE_EXTENSION) return "Workspace keys";
  const slot = row.slot.toLowerCase();
  if (slot === MCP_SERVERS_SLOT) return "MCP";
  if (MODEL_PROVIDER_SLOTS.includes(slot as (typeof MODEL_PROVIDER_SLOTS)[number]))
    return "Model providers";
  return "Service keys";
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

const CREDENTIALS: ListingSpec<CredentialsPayload, Slot> = {
  read: "/workspace/credentials",
  lead: (
    <Section
      title="Coding providers"
      note="This account is yours alone. Each member connects their own, and it is used only for coding tasks."
    >
      <ConnectAccount />
    </Section>
  ),
  group: credentialSection,
  /* An unset slot is nothing to read and nothing to replace, so it draws no row; a workspace
     declaration draws one either way, because it names a key even before it holds a value. */
  rows: (payload) =>
    payload.slots
      .filter((slot) => slot.filled || slot.extension === WORKSPACE_EXTENSION)
      .sort((left, right) => {
        const leftSection = credentialSection(left);
        const rightSection = credentialSection(right);
        if (leftSection !== rightSection) {
          const leftRank = SECTION_ORDER.indexOf(leftSection);
          const rightRank = SECTION_ORDER.indexOf(rightSection);
          const rankedLeft = leftRank === -1 ? SECTION_ORDER.length : leftRank;
          const rankedRight = rightRank === -1 ? SECTION_ORDER.length : rightRank;
          if (rankedLeft !== rankedRight) return rankedLeft - rankedRight;
          return leftSection.localeCompare(rightSection);
        }
        return left.slot.localeCompare(right.slot);
      }),
  rowKey: (row) => row.name,
  search: (row) => [row.slot, row.description, row.extension, row.host, row.env].join(" "),
  list: {
    mark: (row) => (
      <MarkTile>
        <BrandMark provider={slotProvider(row.slot)} className="text-ink" />
      </MarkTile>
    ),
    /* The variable, not the slug core files it under: a workspace key drawn as `acme_api_key`
       beside `OPENAI_API_KEY` reads as a second kind of name for the same thing. */
    primary: { field: "env", render: (env, row) => env || row.slot },
    meta: [
      { field: "description", render: (description) => codeSpans(description) },
      { field: "host", render: (host) => codeSpans(host) },
    ],
  },

  empty: "No credential is set.",
  /* Over the rows rather than among them: an act drawn as a row is filtered out by a search, and
     the member searching for the key they have not added yet is the one who needs it. */
  offer: (payload, context) => {
    const request = context.actions.find((view) => view.name === "request_credentials");
    const unset = payload.slots.filter(
      (slot) => !slot.filled && slot.extension !== WORKSPACE_EXTENSION,
    );
    return (
      <div className="flex gap-sm">
        <DeclareCredential
          busy={context.busy}
          context={context}
          taken={payload.slots}
        />
        {request && unset.length ? (
          <SetCredential
            slots={unset}
            busy={context.busy}
            onPicked={(row) => void context.action(request, credentialRequest(row))}
          />
        ) : null}
      </div>
    );
  },
  views: (payload) => payload.actions,
  actions: (row, context) => {
    const { act, action, busy, actions } = context;
    const request = actions.find((view) => view.name === "request_credentials");
    return (
      <div className={ACTS}>
        {row.filled ? (
          <span data-part="status" className="flex items-center gap-xs text-label text-ink-soft">
            <IconCheck
              role="img"
              aria-label={(row.env || row.slot) + " filled"}
              className="size-icon"
            />
            Filled
          </span>
        ) : (
          <span data-part="status" className="text-label text-ink-soft">
            No value
          </span>
        )}
        {row.extension === WORKSPACE_EXTENSION ? (
          <DeclareCredential busy={busy} context={context} slot={row} />
        ) : null}
        {request ? (
          <Button
            variant="row"
            disabled={busy}
            onClick={() => void action(request, credentialRequest(row))}
          >
            {row.slot === MCP_SERVERS_SLOT ? "Update" : row.filled ? "Replace" : "Set"}
          </Button>
        ) : null}
        <ConfirmButton
          verb={row.extension === WORKSPACE_EXTENSION ? "Remove" : "Clear"}
          variant="row"
          disabled={busy}
          onClick={() =>
            void act({
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
              ? "Save MCP server"
              : request.prompts.length === 1
                ? "Credential value"
                : "Credential values"}
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

/** A pasted endpoint is filed as the host the egress proxy matches on, trimmed where the member
 *  can see it happen rather than saved as a value the proxy will never match. */
function hostOf(said: string) {
  return said
    .trim()
    .replace(/^[a-z][a-z0-9+.-]*:\/\//i, "")
    .replace(/[/?#].*$/, "")
    .replace(/\.$/, "");
}

/** The sealed handoff a row's Set opens, minted and spent here on the value the form already holds
 *  — so a new key is declared and filled by one save and the secret is typed once. */
async function storeValue(agentId: string, actions: ActionView[], row: Slot, value: string) {
  const request = actions.find((view) => view.name === "request_credentials");
  const stranded = "The key is declared. Its value was not stored — set it from its row.";
  if (!request) return stranded;
  const outcome = await postAction(agentId, request.call, credentialRequest(row));
  const sealed = outcome.credentials?.sealed;
  if (!sealed) return outcome.message || stranded;
  let res: Response;
  try {
    res = await fetch(BASE + "/credentials", {
      method: "POST",
      body: new URLSearchParams({ sealed, slot: row.slot, value }),
      credentials: "same-origin",
    });
  } catch {
    return "Network error — try again.";
  }
  return res.ok ? "" : (await res.text().catch(() => "")) || stranded;
}

/** The variable an admin names is the name the declaration is filed under, not a separate one, so
 *  a slot cannot be declared under one name and exported under another. */
function DeclareCredential({
  busy,
  context,
  taken = [],
  slot,
}: {
  busy: boolean;
  context: RowContext;
  taken?: Slot[];
  slot?: Slot;
}) {
  const [open, setOpen] = useState(false);
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
      {/* Mounted only while it stands, so every open starts on empty fields rather than on the
          last key's host and the secret typed into it. */}
      {open ? (
        <Sheet
          open
          title={slot ? "Edit workspace key" : "Add workspace key"}
          onClose={() => setOpen(false)}
        >
          <DeclareForm
            context={context}
            taken={taken}
            slot={slot}
            onSaved={() => setOpen(false)}
          />
        </Sheet>
      ) : null}
    </>
  );
}

function DeclareForm({
  context,
  taken,
  slot,
  onSaved,
}: {
  context: RowContext;
  taken: Slot[];
  slot?: Slot;
  onSaved: () => void;
}) {
  const mainAgent = useMainAgent();
  const [env, setEnv] = useState(slot?.env ?? "");
  const [host, setHost] = useState(slot?.host ?? "");
  const [header, setHeader] = useState(slot?.header ?? DEFAULT_HEADER);
  const [description, setDescription] = useState(slot?.description ?? "");
  const [value, setValue] = useState("");
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [saving, setSaving] = useState(false);
  const name = slotName(env);
  const clash =
    slot === undefined && name !== ""
      ? (taken.find((held) => slotName(held.slot) === name) ?? null)
      : null;
  const ready =
    Boolean(env.trim() && host.trim() && (slot || value.trim())) && clash === null && !saving;

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!mainAgent || !ready) return;
    const declared: Slot = {
      name,
      slot: env.trim().toLowerCase(),
      description: description.trim(),
      extension: WORKSPACE_EXTENSION,
      filled: false,
      host: hostOf(host),
      env: env.trim(),
      header: header.trim() || DEFAULT_HEADER,
    };
    setSaving(true);
    const outcome = await context.act({
      verb: "apply",
      kind: SLOT_KIND,
      name: declared.name,
      spec: {
        slot: declared.slot,
        env: declared.env,
        host: declared.host,
        header: declared.header,
        description: declared.description,
      },
    });
    if (!outcome.applied) {
      setSaving(false);
      setNotice(outcomeNotice(outcome));
      return;
    }
    const failure = slot ? "" : await storeValue(mainAgent.id, context.actions, declared, value);
    setSaving(false);
    if (failure) {
      setNotice({ text: failure, refused: true });
      return;
    }
    onSaved();
  }

  return (
    <form onSubmit={save} className="flex flex-col gap-2xl">
      <OutcomeNotice state={notice} />
      <div className="flex flex-col gap-lg">
        <Field
          label="Variable"
          htmlFor="credential-env"
          description="The name the sandbox exports. Upper case letters, digits and underscore, 3 to 64 characters."
        >
          <Input
            id="credential-env"
            /* Without it the drawer's Close takes the focus, where Enter discards the form. */
            autoFocus={slot === undefined}
            autoComplete="off"
            placeholder="PROVIDER_API_KEY"
            required
            pattern={VARIABLE_PATTERN}
            disabled={slot !== undefined}
            value={env}
            onChange={(event) => setEnv(event.target.value)}
          />
        </Field>
        {clash ? (
          <p role="status" className="m-0 text-label text-ink-soft">
            {env.trim()}{" "}
            {clash.extension === WORKSPACE_EXTENSION
              ? "is already declared. Edit it from its row."
              : "is declared by an installed extension. Choose another name."}
          </p>
        ) : null}
        <Field
          label="Host"
          htmlFor="credential-host"
          description="The provider host the value is sent to. A public DNS name, without scheme or path."
        >
          <Input
            id="credential-host"
            autoFocus={slot !== undefined}
            autoComplete="off"
            placeholder="api.example.com"
            required
            pattern={HOST_PATTERN}
            value={host}
            onChange={(event) => setHost(event.target.value)}
            onBlur={(event) => setHost(hostOf(event.target.value))}
          />
        </Field>
        <Field
          label="Header"
          htmlFor="credential-header"
          description="The request header the value is sent in."
        >
          <Input
            id="credential-header"
            autoComplete="off"
            placeholder={DEFAULT_HEADER}
            pattern={HEADER_PATTERN}
            value={header}
            onChange={(event) => setHeader(event.target.value)}
          />
        </Field>
        <Field
          label="Description"
          htmlFor="credential-description"
          description="What the key is for. Shown in its row on this screen."
        >
          <Input
            id="credential-description"
            autoComplete="off"
            placeholder="Billing API key."
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
        {slot ? null : (
          <Field
            label="Value"
            htmlFor="credential-value"
            description="Stored encrypted and never shown again. The sandbox gets a placeholder; the proxy sends the real value to this host alone."
          >
            <Input
              id="credential-value"
              type="password"
              autoComplete="off"
              placeholder="Paste the key"
              required
              value={value}
              onChange={(event) => setValue(event.target.value)}
            />
          </Field>
        )}
      </div>
      <div className="flex justify-end">
        <Button type="submit" variant="send" size="bar" busy={saving} disabled={!ready}>
          Save
        </Button>
      </div>
    </form>
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
  const [asking, setAsking] = useState(false);
  return (
    <div className="flex">
      <Button variant="outline" size="bar" disabled={busy} onClick={() => setAsking(true)}>
        Add service key
      </Button>
      {asking ? (
        <Sheet open title="Add service key" onClose={() => setAsking(false)}>
          <PickCredential
            slots={slots}
            busy={busy}
            onPicked={(row) => {
              setAsking(false);
              onPicked(row);
            }}
          />
        </Sheet>
      ) : null}
    </div>
  );
}

function PickCredential({
  slots,
  busy,
  onPicked,
}: {
  slots: Slot[];
  busy: boolean;
  onPicked: (row: Slot) => void;
}) {
  const [picked, setPicked] = useState("");
  const row = slots.find((slot) => slot.slot === picked);
  return (
    <div className="flex flex-col gap-2xl">
      <Field label="Credential" htmlFor="credential-slot">
        <Select value={picked} onValueChange={setPicked}>
          <SelectTrigger id="credential-slot">
            <SelectValue placeholder="Choose a credential" />
          </SelectTrigger>
          <SelectContent>
            {slots.map((slot) => (
              <SelectItem key={slot.slot} value={slot.slot}>
                <span className="flex min-w-0 items-center gap-sm">
                  <BrandMark provider={slotProvider(slot.slot)} className="size-(--size-icon)" />
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
          size="bar"
          disabled={!row || busy}
          onClick={() => row && onPicked(row)}
        >
          Continue
        </Button>
      </div>
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

export function WorkspaceCredentials({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  return <Listing spec={CREDENTIALS} place={place} onPlace={onPlace} />;
}
