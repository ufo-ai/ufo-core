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
import { Field, Input } from "@/components/ui/field";
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

/** The extension that resolves a slot this workspace declared for itself, rather than one a
 *  manifest declares for the whole deploy. Those are the rows an admin may edit and remove here,
 *  through the `credential_slot` kind that holds the declaration. */
const WORKSPACE_EXTENSION = "workspace_credentials";
const SLOT_KIND = "credential_slot";
const DEFAULT_HEADER = "Authorization";

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

const SECTION_ORDER = ["Model providers", "Service keys", "Workspace keys", "MCP"];
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

/** The provider a slot's value authenticates with: the longest provider the portal draws, that the
 *  slot's own name starts with. A slot named for its provider (`slack`) and every slot naming a
 *  value under one (`DATADOG_API_KEY`, `DATADOG_APPLICATION_KEY`) land on that provider's one mark;
 *  a slot the portal draws nothing for keeps the name it has, and takes the connector glyph.
 *
 *  Read off the two sets that answer for a mark rather than off a table of endings — a table would
 *  be a second list to keep, and it read `SLACK_BOT_TOKEN` as a provider called `slack_bot`. */
const SLOT_PROVIDERS = [...BRAND_MARKS, ...Object.keys(PROVIDER_GLYPHS)].sort(
  (left, right) => right.length - left.length,
);

function slotProvider(slot: string) {
  const name = slot.toLowerCase();
  return (
    SLOT_PROVIDERS.find((provider) => name === provider || name.startsWith(provider + "_")) ?? name
  );
}

/** The mark over a prompt: one provider's, where every slot the request asks for is that
 *  provider's. A request spanning two providers names none of them here. */
function askedProvider(prompts: CredentialPrompt[]) {
  const asked = new Set(prompts.map((prompt) => slotProvider(prompt.slot)));
  return asked.size === 1 ? [...asked][0] : null;
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
      .filter((slot) => slot.filled || slot.extension === WORKSPACE_EXTENSION)
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
  search: (row) => [row.slot, row.description, row.extension, row.host, row.env].join(" "),
  list: {
    mark: (row) => (
      <MarkTile>
        <BrandMark provider={slotProvider(row.slot)} className="text-ink" />
      </MarkTile>
    ),
    primary: { field: "slot" },
    meta: [
      { field: "description", render: (description) => codeSpans(description) },
      { field: "host", render: (host, row) => (host ? codeSpans(`${row.env} → ${host}`) : null) },
    ],
  },
  empty: "No credential is set.",
  /* Every slot with a value has a row, and a slot with none has none — so the act that fills one
     stands here, for the member a surface sent to this screen to fill it. */
  offer: (payload, { act, action, busy, actions }) => {
    const request = actions.find((view) => view.name === "request_credentials");
    const unset = payload.slots.filter((slot) => !slot.filled);
    return (
      <div className="flex gap-sm">
        {request && unset.length ? (
          <SetCredential
            slots={unset}
            busy={busy}
            onPicked={(row) => action(request, credentialRequest(row))}
          />
        ) : null}
        <DeclareCredential busy={busy} onDeclared={act} />
      </div>
    );
  },
  views: (payload) => payload.actions,
  actions: (row, { act, action, busy, actions }) => {
    const request = actions.find((view) => view.name === "request_credentials");
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
          <DeclareCredential busy={busy} onDeclared={act} slot={row} />
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
                ? "Set Credential"
                : "Set Credentials"}
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

/** The head of a credential prompt: the provider's mark beside what the prompt says, so a member
 *  reading a dialog raised from chat or from a row sees whose value they are typing. A prompt with
 *  no one provider draws the words alone. */
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

/** The name the `credential_slot` kind addresses a declaration by — the slot's own name as a slug,
 *  derived here the same way the kind derives it, and the same name the `credential` kind gives the
 *  slot, so the row the panel writes is the row it reads back. */
function slotName(slot: string) {
  return slot
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** The workspace's own credential slot: an admin names the variable the sandbox exports, the host
 *  its value rides to, and the header it rides in. The value is not typed here — the declaration
 *  lands first, and the value goes through the same private prompt every other slot's does. */
function DeclareCredential({
  busy,
  onDeclared,
  slot,
}: {
  busy: boolean;
  onDeclared: (envelope: unknown) => void;
  slot?: Slot;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(slot?.slot ?? "");
  const [env, setEnv] = useState(slot?.env ?? "");
  const [host, setHost] = useState(slot?.host ?? "");
  const [header, setHeader] = useState(slot?.header ?? DEFAULT_HEADER);
  const [description, setDescription] = useState(slot?.description ?? "");
  const ready = Boolean(name.trim() && env.trim() && host.trim());

  function declare() {
    setOpen(false);
    onDeclared({
      verb: "apply",
      kind: SLOT_KIND,
      name: slotName(name),
      spec: {
        slot: name.trim(),
        env: env.trim(),
        host: host.trim(),
        header: header.trim() || DEFAULT_HEADER,
        description: description.trim(),
      },
    });
  }

  return (
    <>
      {slot ? (
        <Button variant="row" disabled={busy} onClick={() => setOpen(true)}>
          Edit
        </Button>
      ) : (
        <Button variant="outline" size="bar" disabled={busy} onClick={() => setOpen(true)}>
          Add a credential
        </Button>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{slot ? "Edit Credential" : "Add Credential"}</DialogTitle>
            <DialogDescription>
              The sandbox gets the variable holding a placeholder. The proxy sends the real value to
              this host alone, so the key never enters the sandbox. Set the value after saving.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-lg">
            <Field label="Name" htmlFor="credential-name" description="Lower case, e.g. acme_api_key.">
              <Input
                id="credential-name"
                autoComplete="off"
                placeholder="acme_api_key"
                required
                disabled={slot !== undefined}
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </Field>
            <Field label="Variable" htmlFor="credential-env" description="What the sandbox exports.">
              <Input
                id="credential-env"
                autoComplete="off"
                placeholder="ACME_API_KEY"
                required
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
          </div>
          <DialogFooter>
            <Button variant="send" disabled={!ready || busy} onClick={declare}>
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

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
