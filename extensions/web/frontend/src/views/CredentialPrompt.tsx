import { useState, type ComponentProps, type FormEvent } from "react";

import { IconCheck, IconKey } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemTitle,
  MarkTile,
} from "@/components/ui/item";
import { BASE } from "@/lib/api";
import type { CredentialPrompt } from "@/lib/types";

const VALUE_PLACEHOLDER = "Paste the key";
const SET_CREDENTIAL = "Set credential";
const STORED = "Stored.";
const REFUSED = "Unable to store credential.";
const NETWORK_REFUSED = "Network error — try again.";

/** The box a secret is typed into, drawn the same way on the row a turn draws and in the
 *  credentials screen's dialog. It is uncontrolled because React mirrors a controlled `value` into
 *  the element's `value` attribute, which puts the secret in the serialized DOM. */
export function CredentialValueFields({
  idPrefix,
  prompt,
  onChange,
  surface,
  disabled,
  className,
}: {
  idPrefix: string;
  prompt: CredentialPrompt;
  onChange: (value: string) => void;
  surface?: ComponentProps<typeof Input>["surface"];
  disabled?: boolean;
  className?: string;
}) {
  return (
    <Input
      id={idPrefix}
      name={prompt.slot}
      type="password"
      /* `off` is ignored on a password box, so the member's saved site password lands in the field
         a turn asked a provider key for; `new-password` is the token that holds it out. */
      autoComplete="new-password"
      aria-label={prompt.prompt}
      surface={surface}
      placeholder={VALUE_PLACEHOLDER}
      required
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      className={className}
    />
  );
}

export type CredentialPromptFormProps = {
  sealed: string;
  prompt: CredentialPrompt;
  onStored: (slot: string) => void;
};

/** The secret a turn cannot go on without, drawn as the row every other handoff takes: the mark at
 *  the leading edge, what is asked and the box it is answered in down the middle, and the trailing
 *  edge marked once the workspace holds it. */
export function CredentialPromptForm({ sealed, prompt, onStored }: CredentialPromptFormProps) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState("");
  const [settled, setSettled] = useState(false);
  const stored = prompt.stored === true || settled;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !value.trim()) return;
    setBusy(true);
    setRefusal("");
    const body = new URLSearchParams({ sealed, slot: prompt.slot, value });
    let res: Response;
    try {
      res = await fetch(BASE + "/credentials", {
        method: "POST",
        body,
        credentials: "same-origin",
      });
    } catch {
      setRefusal(NETWORK_REFUSED);
      setBusy(false);
      return;
    }
    if (!res.ok) {
      setRefusal((await res.text()) || REFUSED);
      setBusy(false);
      return;
    }
    setValue("");
    setSettled(true);
    setBusy(false);
    onStored(prompt.slot);
  }

  return (
    <ItemGroup className="-mx-2xl">
      <Item size="row" className="items-start">
        <ItemMedia>
          <MarkTile compact>
            <IconKey aria-hidden className="size-(--size-glyph) text-ink-soft" />
          </MarkTile>
        </ItemMedia>
        <ItemContent>
          <ItemTitle>{prompt.slot}</ItemTitle>
          <ItemDescription whole>{stored ? STORED : prompt.prompt}</ItemDescription>
          {stored ? null : (
            <form onSubmit={submit} className="mt-sm flex items-stretch gap-sm @max-md:flex-col">
              <CredentialValueFields
                idPrefix={"credential-" + prompt.slot}
                prompt={prompt}
                onChange={setValue}
                surface="answer"
                disabled={busy}
                className="max-w-none flex-1"
              />
              <Button type="submit" variant="send" busy={busy} disabled={!value.trim()}>
                {SET_CREDENTIAL}
              </Button>
            </form>
          )}
          {stored ? null : (
            <p role="alert" className="m-0 mt-2xs min-h-lh text-label text-attention-ink">
              {refusal}
            </p>
          )}
        </ItemContent>
        {stored ? (
          <ItemActions>
            <IconCheck aria-hidden className="size-icon shrink-0 text-ink" />
          </ItemActions>
        ) : null}
      </Item>
    </ItemGroup>
  );
}
