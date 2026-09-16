import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { BASE } from "@/lib/api";
import type { CredentialPrompt } from "@/lib/types";

export function CredentialValueFields({
  idPrefix,
  prompt,
  onChange,
  className,
}: {
  idPrefix: string;
  prompt: CredentialPrompt;
  onChange: (value: string) => void;
  className?: string;
}) {
  const [value, setValue] = useState("");

  return (
    <Input
      id={idPrefix}
      type="password"
      autoComplete="off"
      placeholder={prompt.slot}
      required
      value={value}
      onChange={(event) => {
        setValue(event.target.value);
        onChange(event.target.value);
      }}
      className={className}
    />
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
  const heading = prompt.prompt;
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
      "Stored " + prompt.slot + ".",
    );
  }

  return (
    <div>
      <div role="status" className="mb-hair text-ui text-ink-soft">
        {label}
      </div>
      {stored ? null : (
        <form onSubmit={submit} className="flex items-stretch gap-xs">
          <CredentialValueFields
            idPrefix={"credential-" + prompt.slot}
            prompt={prompt}
            onChange={setValue}
            className="max-w-none flex-1"
          />
          <Button type="submit" variant="send" busy={busy}>
            Store
          </Button>
        </form>
      )}
    </div>
  );
}
