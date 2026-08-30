import { useState, type FormEvent } from "react";

import { Button, ConfirmButton } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { BASE } from "@/lib/api";
import type { CredentialPrompt } from "@/lib/types";

export const MCP_SERVERS_SLOT = "mcp_servers";

type CredentialFields = {
  value: string;
  name: string;
  url: string;
  auth: string;
};

const EMPTY_FIELDS: CredentialFields = { value: "", name: "", url: "", auth: "" };

export function CredentialValueFields({
  idPrefix,
  prompt,
  onChange,
  onRemove,
  busy,
  className,
}: {
  idPrefix: string;
  prompt: CredentialPrompt;
  onChange: (value: string) => void;
  onRemove?: (name: string) => void;
  busy?: boolean;
  className?: string;
}) {
  const [fields, setFields] = useState(EMPTY_FIELDS);

  function change(field: keyof CredentialFields, value: string) {
    const next = { ...fields, [field]: value };
    setFields(next);
    if (prompt.slot !== MCP_SERVERS_SLOT) {
      onChange(next.value);
      return;
    }
    const name = next.name.trim();
    const url = next.url.trim();
    onChange(
      name && url
        ? JSON.stringify({ name, url, ...(next.auth.trim() ? { auth: next.auth.trim() } : {}) })
        : "",
    );
  }

  if (prompt.slot !== MCP_SERVERS_SLOT) {
    return (
      <Input
        id={idPrefix}
        type="password"
        autoComplete="off"
        placeholder={prompt.slot}
        required
        value={fields.value}
        onChange={(event) => change("value", event.target.value)}
        className={className}
      />
    );
  }

  return (
    <div className="flex flex-col gap-lg">
      <Field label="Server name" htmlFor={idPrefix + "-name"}>
        <Input
          id={idPrefix + "-name"}
          autoComplete="off"
          placeholder="vercel"
          required
          value={fields.name}
          onChange={(event) => change("name", event.target.value)}
          className={className}
        />
      </Field>
      <Field label="Server URL" htmlFor={idPrefix + "-url"}>
        <Input
          id={idPrefix + "-url"}
          type="url"
          autoComplete="url"
          placeholder="https://mcp.example.com"
          required
          value={fields.url}
          onChange={(event) => change("url", event.target.value)}
          className={className}
        />
      </Field>
      <Field
        label="Access token"
        htmlFor={idPrefix + "-auth"}
        description="Optional. Leave blank to keep the saved token when you update this server."
      >
        <Input
          id={idPrefix + "-auth"}
          type="password"
          autoComplete="off"
          value={fields.auth}
          onChange={(event) => change("auth", event.target.value)}
          className={className}
        />
      </Field>
      {onRemove ? (
        <ConfirmButton
          verb="Remove server"
          variant="row"
          busy={busy}
          disabled={!fields.name.trim()}
          onClick={() => onRemove(fields.name.trim())}
          className="self-end"
        />
      ) : null}
    </div>
  );
}

export type CredentialPromptFormProps = {
  sealed: string;
  prompt: CredentialPrompt;
  onStored: (slot: string) => void;
};

export function CredentialPromptForm({
  sealed,
  prompt,
  onStored,
}: CredentialPromptFormProps) {
  const heading =
    prompt.slot === MCP_SERVERS_SLOT ? "Add or update an MCP server." : prompt.prompt;
  const [label, setLabel] = useState(heading);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [stored, setStored] = useState(false);

  async function store(next: string, success: string) {
    if (busy || !next.trim()) return;
    setBusy(true);
    const body = new URLSearchParams({ sealed, slot: prompt.slot, value: next });
    let res: Response;
    try {
      res = await fetch(BASE + "/credentials", {
        method: "POST",
        body,
        credentials: "same-origin",
      });
    } catch {
      setLabel(heading + " Network error. Try again.");
      setBusy(false);
      return;
    }
    if (!res.ok) {
      setLabel(heading + " " + (await res.text()) + ".");
      setBusy(false);
      return;
    }
    setLabel(success);
    setStored(true);
    setBusy(false);
    onStored(prompt.slot);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    await store(
      value,
      prompt.slot === MCP_SERVERS_SLOT ? "MCP server saved." : "Stored " + prompt.slot + ".",
    );
  }

  return (
    <div>
      <div role="status" className="mb-hair text-ui text-ink-soft">
        {label}
      </div>
      {stored ? null : (
        <form
          onSubmit={submit}
          className={
            prompt.slot === MCP_SERVERS_SLOT
              ? "flex flex-col gap-lg"
              : "flex items-stretch gap-xs"
          }
        >
          <CredentialValueFields
            idPrefix={"credential-" + prompt.slot}
            prompt={prompt}
            onChange={setValue}
            onRemove={
              prompt.slot === MCP_SERVERS_SLOT
                ? (name) => void store(JSON.stringify({ name, remove: true }), "MCP server removed.")
                : undefined
            }
            busy={busy}
            className="max-w-none flex-1"
          />
          <Button
            type="submit"
            variant="send"
            busy={busy}
            className={prompt.slot === MCP_SERVERS_SLOT ? "self-end" : undefined}
          >
            {prompt.slot === MCP_SERVERS_SLOT ? "Save server" : "Store"}
          </Button>
        </form>
      )}
    </div>
  );
}
