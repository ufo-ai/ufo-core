import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { BASE } from "@/lib/api";
import type { CredentialPrompt } from "@/lib/types";

export type CredentialPromptFormProps = {
  sealed: string;
  prompt: CredentialPrompt;
  onStored: (slot: string) => void;
};

export function CredentialPromptForm({ sealed, prompt, onStored }: CredentialPromptFormProps) {
  const [label, setLabel] = useState(prompt.prompt);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [stored, setStored] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!value.trim()) return;
    setBusy(true);
    const body = new URLSearchParams({ sealed, slot: prompt.slot, value });
    let res: Response;
    try {
      res = await fetch(BASE + "/credentials", {
        method: "POST",
        body,
        credentials: "same-origin",
      });
    } catch {
      setLabel(prompt.prompt + " — network error, try again.");
      setBusy(false);
      return;
    }
    if (!res.ok) {
      setLabel(prompt.prompt + " — " + (await res.text()) + ".");
      setBusy(false);
      return;
    }
    setLabel("Stored " + prompt.slot + ".");
    setStored(true);
    onStored(prompt.slot);
  }

  return (
    <div>
      <div>{label}</div>
      {stored ? null : (
        <form onSubmit={submit} className="flex gap-xs">
          <input
            type="password"
            autoComplete="off"
            placeholder={prompt.slot}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            className="flex-1 rounded-panel border border-edge-control bg-field px-md py-xs text-field-ink"
          />
          <Button type="submit" variant="send" disabled={busy}>
            Store
          </Button>
        </form>
      )}
    </div>
  );
}
