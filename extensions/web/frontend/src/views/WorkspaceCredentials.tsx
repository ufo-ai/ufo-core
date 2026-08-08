import { useEffect, useRef, useState, type FormEvent } from "react";

import { Button, ConfirmButton } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/field";
import { OutcomeNotice, type NoticeState, QUIET } from "@/kernel/panel";
import type { ListingSpec } from "@/kernel/listing";
import { BASE } from "@/lib/api";
import type { CredentialPrompt } from "@/lib/types";

type Slot = {
  name: string;
  slot: string;
  description: string;
  extension: string;
  filled: boolean;
};

type CredentialsPayload = { slots: Slot[] };

const GROUPS: Record<string, string> = {
  coding: "Coding",
  exa: "Exa",
  keyed_connectors: "Connectors",
  mcp: "MCP",
  sources: "Sources",
};

const GROUP_KEYS = Object.keys(GROUPS);

export const CREDENTIALS: ListingSpec<CredentialsPayload, Slot> = {
  read: "/workspace/credentials",
  rows: (payload) =>
    [...payload.slots].sort((left, right) => {
      const leftRank = GROUP_KEYS.indexOf(left.extension);
      const rightRank = GROUP_KEYS.indexOf(right.extension);
      const normalizedLeftRank = leftRank === -1 ? GROUP_KEYS.length : leftRank;
      const normalizedRightRank = rightRank === -1 ? GROUP_KEYS.length : rightRank;
      if (normalizedLeftRank !== normalizedRightRank)
        return normalizedLeftRank - normalizedRightRank;
      if (leftRank === -1 && left.extension !== right.extension)
        return left.extension.localeCompare(right.extension);
      if (left.filled !== right.filled) return left.filled ? -1 : 1;
      return left.slot.localeCompare(right.slot);
    }),
  rowKey: (row) => row.name,
  search: (row) => [row.slot, row.description, row.extension].join(" "),
  chips: [
    { label: "Filled", has: (row) => row.filled },
    { label: "Not set", has: (row) => !row.filled },
  ],
  cards: {
    mark: { shape: "square" },
    primary: { field: "slot" },
    status: {
      field: "filled",
      render: (filled) => (filled ? "Filled" : "Not set"),
    },
    body: { field: "description", render: (description) => descriptionWithCode(description) },
  },
  empty: "No credential slots are declared.",
  actions: (row, { act, busy }) => (
    <div className="flex flex-wrap gap-xs">
      <Button
        variant="row"
        disabled={busy}
        onClick={() =>
          act({ verb: "request", kind: "credential", name: row.name })
        }
      >
        {row.filled ? "Replace" : "Set"}
      </Button>
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

function descriptionWithCode(description: string) {
  const segments = description.split("`");
  return segments.map((segment, index) =>
    index % 2 === 1 && index < segments.length - 1 ? (
      <code key={index} className="font-mono text-mono">
        {segment}
      </code>
    ) : (
      segment
    ),
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

  async function store(prompt: CredentialPrompt): Promise<string | null> {
    const body = new URLSearchParams({
      sealed,
      slot: prompt.slot,
      value: values[prompt.slot],
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
        {pending.map((prompt) => (
          <Field key={prompt.slot} label={prompt.prompt} htmlFor={prompt.slot}>
            <Input
              id={prompt.slot}
              type="password"
              autoComplete="off"
              required
              value={values[prompt.slot] ?? ""}
              onChange={(event) =>
                setValues((current) => ({
                  ...current,
                  [prompt.slot]: event.target.value,
                }))
              }
            />
          </Field>
        ))}
      </form>
      <DialogFooter>
        <Button
          type="submit"
          form="set-credentials"
          variant="send"
          busy={busy}
          disabled={!ready}
        >
          {pending.length === 1 ? "Set credential" : "Set credentials"}
        </Button>
      </DialogFooter>
    </>
  );
}
