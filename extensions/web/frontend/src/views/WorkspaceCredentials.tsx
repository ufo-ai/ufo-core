import { useState } from "react";

import type { Placement } from "@/views/Workspace";
import { CredentialPromptForm } from "@/views/CredentialPrompt";
import { Button } from "@/components/ui/button";
import { Table, Td, Th } from "@/components/ui/table";
import { Notice, PanelEmpty, usePanelRead } from "@/kernel/panel";
import { postIntent } from "@/lib/api";
import { useMainAgent } from "@/lib/mainAgent";
import type { CredentialRequest } from "@/lib/types";

type Slot = {
  name: string;
  slot: string;
  description: string;
  extension: string;
  filled: boolean;
};

export function WorkspaceCredentials({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const mainAgent = useMainAgent();
  const [notice, setNotice] = useState(place.notice ?? "");
  const [request, setRequest] = useState<CredentialRequest | null>(null);
  const [busy, setBusy] = useState(false);
  const state = usePanelRead<{ slots: Slot[] }>("/workspace/credentials");

  async function act(entry: Slot, verb: string) {
    if (!mainAgent) return;
    setBusy(true);
    const outcome = await postIntent(mainAgent.id, {
      verb,
      kind: "credential",
      name: entry.name,
    });
    setBusy(false);
    if (outcome.credentials) {
      setRequest(outcome.credentials);
      return;
    }
    if (outcome.applied) {
      onPlace({ notice: outcome.message });
      return;
    }
    setNotice(outcome.message);
  }

  if (state.phase === "loading") return null;
  if (state.phase === "failed") return <PanelEmpty>{state.message}</PanelEmpty>;

  const slots = state.payload.slots;
  if (!slots.length) return <PanelEmpty>No credential slots are declared.</PanelEmpty>;

  return (
    <>
      <Table>
        <thead>
          <tr>
            {["slot", "description", "extension", "state", ""].map((column, index) => (
              <Th key={index}>{column}</Th>
            ))}
          </tr>
        </thead>
        <tbody>
          {slots.map((entry) => (
            <tr key={entry.name}>
              <Td>{entry.slot}</Td>
              <Td>{entry.description}</Td>
              <Td>{entry.extension}</Td>
              <Td>{entry.filled ? "filled" : "empty"}</Td>
              <Td>
                <div className="flex flex-wrap gap-xs">
                  <Button variant="row" disabled={busy} onClick={() => act(entry, "request")}>
                    {entry.filled ? "Replace" : "Set"}
                  </Button>
                  {entry.filled ? (
                    <Button variant="row" disabled={busy} onClick={() => act(entry, "delete")}>
                      Clear
                    </Button>
                  ) : null}
                </div>
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
      {request ? (
        <div>
          <div>{request.reason}</div>
          {request.prompts.map((prompt) => (
            <CredentialPromptForm
              key={prompt.slot}
              sealed={request.sealed}
              prompt={prompt}
              onStored={(slot) => onPlace({ notice: "Stored " + slot + "." })}
            />
          ))}
        </div>
      ) : null}
      <Notice>{notice}</Notice>
    </>
  );
}
