import { useEffect, useRef, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Table, Td, Th } from "@/components/ui/table";
import { Notice, PanelEmpty, usePanelRead } from "@/kernel/panel";
import { BASE, postIntent } from "@/lib/api";
import type { Agent } from "@/lib/types";

type Connection = {
  provider: string;
  account_id: string | null;
  owner_email: string | null;
  shared: boolean;
  connected_at: string;
  grant: string;
};

type ConnectionsPayload = { connections: Connection[] };

export function Connections({ agent }: { agent: Agent }) {
  const [reloads, setReloads] = useState(0);
  const [handoff, setHandoff] = useState("");
  const [consentUrl, setConsentUrl] = useState<string | null>(null);
  const [provider, setProvider] = useState("");
  const [shared, setShared] = useState(false);
  const [busy, setBusy] = useState(false);
  const [watching, setWatching] = useState<string | null>(null);
  const state = usePanelRead<ConnectionsPayload>("/agents/" + agent.id + "/connections", reloads);
  const source = useRef<EventSource | null>(null);

  useEffect(() => {
    if (watching === null) return;
    const stream = new EventSource(BASE + "/turns/" + watching + "/stream");
    source.current = stream;
    const done = () => {
      stream.close();
      source.current = null;
    };
    stream.addEventListener("connect", (event) => {
      setConsentUrl(JSON.parse((event as MessageEvent).data).url);
      setHandoff("");
      done();
    });
    stream.addEventListener("connect_error", (event) => {
      setHandoff(JSON.parse((event as MessageEvent).data).message);
      done();
    });
    stream.addEventListener("terminal", done);
    stream.onerror = done;
    return () => {
      stream.close();
      source.current = null;
    };
  }, [watching]);

  async function connect(event: FormEvent) {
    event.preventDefault();
    const named = provider.trim();
    if (!named) return;
    setBusy(true);
    setConsentUrl(null);
    const outcome = await postIntent(agent.id, {
      verb: "connect",
      kind: "connection",
      name: named,
      spec: { shared },
    });
    setBusy(false);
    if (!outcome.applied) {
      setHandoff(outcome.message);
      return;
    }
    setHandoff("Consent opens privately for you.");
    setWatching(outcome.turn_id ?? null);
  }

  async function act(envelope: unknown) {
    const outcome = await postIntent(agent.id, envelope);
    setHandoff(outcome.message);
    setReloads((count) => count + 1);
  }

  const entries = state.phase === "ready" ? state.payload.connections : [];

  return (
    <>
      <form onSubmit={connect} className="mb-lg flex gap-sm">
        <input
          placeholder="Provider (github, notion, …)"
          value={provider}
          onChange={(event) => setProvider(event.target.value)}
          className="flex-1 rounded-panel border border-edge-control bg-field px-md py-sm text-field-ink"
        />
        <label htmlFor="connect-shared">
          <input
            id="connect-shared"
            type="checkbox"
            checked={shared}
            onChange={(event) => setShared(event.target.checked)}
          />
          Share with agent
        </label>
        <Button type="submit" variant="send" disabled={busy}>
          Connect
        </Button>
      </form>
      <Notice>
        {consentUrl ? (
          <a href={consentUrl} target="_blank" rel="noopener">
            Open the provider consent page
          </a>
        ) : (
          handoff
        )}
      </Notice>
      {state.phase === "failed" ? <PanelEmpty>{state.message}</PanelEmpty> : null}
      {state.phase === "ready" && !entries.length ? (
        <PanelEmpty>No accounts are connected to this agent.</PanelEmpty>
      ) : null}
      {entries.length ? (
        <Table>
          <thead>
            <tr>
              {["provider", "account", "owner", "access", "connected", ""].map((column, index) => (
                <Th key={index}>{column}</Th>
              ))}
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.grant}>
                <Td>{entry.provider}</Td>
                <Td>{entry.account_id ?? "—"}</Td>
                <Td>{entry.owner_email ?? "—"}</Td>
                <Td>{entry.shared ? "agent-shared" : "private"}</Td>
                <Td>{entry.connected_at.slice(0, 10)}</Td>
                <Td>
                  {entry.owner_email === null ? null : (
                    <div className="flex flex-wrap gap-xs">
                      <Button
                        variant="row"
                        onClick={() =>
                          act({
                            verb: "apply",
                            kind: "connector_grant",
                            name: entry.grant,
                            spec: {
                              provider: entry.provider,
                              account_id: entry.account_id,
                              shared: !entry.shared,
                            },
                          })
                        }
                      >
                        {entry.shared ? "Make private" : "Share with agent"}
                      </Button>
                      <Button
                        variant="row"
                        onClick={() =>
                          act({ verb: "delete", kind: "connector_grant", name: entry.grant })
                        }
                      >
                        Revoke
                      </Button>
                    </div>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      ) : null}
    </>
  );
}
