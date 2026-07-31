import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox, Input, Select, Textarea } from "@/components/ui/field";
import { Table, Td, Th } from "@/components/ui/table";
import { Notice, PanelEmpty, usePanelRead } from "@/kernel/panel";
import { getJson, postIntent } from "@/lib/api";
import type { Agent, AdminPayload, Member } from "@/lib/types";

export function Admin() {
  const [reloads, setReloads] = useState(0);
  const [notice, setNotice] = useState("");
  const [copying, setCopying] = useState<Agent | null>(null);
  const state = usePanelRead<AdminPayload>("/api/admin", reloads);

  async function intent(agentId: string, envelope: unknown, prefix?: string) {
    const outcome = await postIntent(agentId, envelope);
    setNotice(prefix && outcome.applied ? prefix + " " + outcome.message : outcome.message);
    if (outcome.applied) setReloads((count) => count + 1);
  }

  if (state.phase === "loading") return null;
  if (state.phase === "failed") {
    return (
      <main className="flex flex-col gap-3xl overflow-y-auto p-2xl">
        <PanelEmpty>{state.message}</PanelEmpty>
      </main>
    );
  }

  const payload = state.payload;
  const mainAgent = payload.agents.find((agent) => agent.main);
  const seats = payload.seats;
  const gated = seats.limit !== null || seats.included !== null;

  return (
    <main className="flex flex-col gap-3xl overflow-y-auto p-2xl" data-testid="admin">
      <Notice>{notice}</Notice>

      <h2 className="m-0 text-section">Agents</h2>
      <Table>
        <thead>
          <tr>
            {["agent", "model", "public internet", "surfaces", "web audience", ""].map(
              (column, index) => (
                <Th key={index}>{column}</Th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {payload.agents.map((agent) => (
            <AgentRow
              key={agent.id}
              agent={agent}
              onCopy={() => {
                setCopying(agent);
                setNotice("Copying " + agent.name + " — configuration only.");
              }}
              onAudience={(verb, email) => intent(agent.id, { verb, email })}
            />
          ))}
        </tbody>
      </Table>

      {mainAgent ? (
        <CreateAgent
          payload={payload}
          copying={copying}
          onCreated={(name, spec) =>
            intent(
              mainAgent.id,
              { verb: "apply", kind: "agent", name, spec },
              "Created " + name + ".",
            )
          }
        />
      ) : null}

      <h2 className="m-0 text-section">
        {"Members" +
          (gated ? "" : " · seats ungated") +
          (seats.limit !== null ? " · " + seats.limit + " seat limit" : "") +
          (seats.included !== null ? " · " + seats.included + " included" : "")}
      </h2>
      <Table>
        <thead>
          <tr>
            {(gated ? ["member", "role", "seat", ""] : ["member", "role", ""]).map(
              (column, index) => (
                <Th key={index}>{column}</Th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {payload.members.map((member) => (
            <MemberRow
              key={member.email}
              member={member}
              gated={gated}
              onApply={(spec) =>
                mainAgent
                  ? intent(mainAgent.id, {
                      verb: "apply",
                      kind: "member",
                      name: member.id,
                      spec,
                    })
                  : undefined
              }
            />
          ))}
        </tbody>
      </Table>

      <h2 className="m-0 text-section">Billing</h2>
      {payload.caps.length ? (
        <Table>
          <thead>
            <tr>
              {["cap", "subject", "window", "limit", "on breach"].map((column) => (
                <Th key={column}>{column}</Th>
              ))}
            </tr>
          </thead>
          <tbody>
            {payload.caps.map((cap, index) => (
              <tr key={index}>
                <Td>{cap.scope}</Td>
                <Td>{cap.subject || "—"}</Td>
                <Td>{cap.window_seconds / 3600 + "h"}</Td>
                <Td>{"$" + (cap.limit_micro_usd / 1e6).toFixed(2)}</Td>
                <Td>{cap.on_breach}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      ) : (
        <div>No spend caps are set.</div>
      )}
      <div>The plan, invoices, and payment methods are managed with the agent in chat.</div>

      <h2 className="m-0 text-section">Deploy</h2>
      <div>
        Sandbox public internet: {payload.deploy.sandbox_internet ? "allowed" : "blocked"}
      </div>
      <Table>
        <thead>
          <tr>
            {["extension", "version", "public internet"].map((column) => (
              <Th key={column}>{column}</Th>
            ))}
          </tr>
        </thead>
        <tbody>
          {payload.deploy.extensions.map((extension) => (
            <tr key={extension.name}>
              <Td>{extension.name}</Td>
              <Td>{extension.version}</Td>
              <Td>{extension.sandbox_internet ? "allowed" : "blocked"}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
    </main>
  );
}

function AgentRow({
  agent,
  onCopy,
  onAudience,
}: {
  agent: Agent;
  onCopy: () => void;
  onAudience: (verb: string, email: string) => void;
}) {
  const [email, setEmail] = useState("");
  return (
    <tr>
      <Td>{agent.name + (agent.main ? " ·" : "")}</Td>
      <Td>{agent.model}</Td>
      <Td>{agent.internet_access_allowed ? "allowed" : "blocked"}</Td>
      <Td>{agent.installations.join(", ") || "—"}</Td>
      <Td>{agent.main ? "every member" : agent.web_audience.concat("admins").join(", ")}</Td>
      <Td>
        <div className="flex flex-wrap items-baseline gap-xs">
          <Button variant="row" onClick={onCopy}>
            Copy
          </Button>
          {agent.main ? null : (
            <>
              <Input
                placeholder="email@work.com"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="max-w-[20ch] px-xs py-hair"
              />
              <Button
                variant="row"
                onClick={() => email.trim() && onAudience("grant_web_access", email.trim())}
              >
                Grant
              </Button>
              <Button
                variant="row"
                onClick={() => email.trim() && onAudience("revoke_web_access", email.trim())}
              >
                Revoke
              </Button>
            </>
          )}
        </div>
      </Td>
    </tr>
  );
}

function MemberRow({
  member,
  gated,
  onApply,
}: {
  member: Member;
  gated: boolean;
  onApply: (spec: { admin: boolean; seated: boolean }) => void;
}) {
  return (
    <tr>
      <Td>{member.email}</Td>
      <Td>{member.admin ? "admin" : "member"}</Td>
      {gated ? <Td>{member.seated ? "seated" : "—"}</Td> : null}
      <Td>
        <div className="flex flex-wrap gap-xs">
          <Button
            variant="row"
            onClick={() => onApply({ admin: !member.admin, seated: Boolean(member.seated) })}
          >
            {member.admin ? "Remove admin" : "Make admin"}
          </Button>
          <Button
            variant="row"
            onClick={() => onApply({ admin: member.admin, seated: !member.seated })}
          >
            {member.seated ? "Unseat" : "Seat"}
          </Button>
        </div>
      </Td>
    </tr>
  );
}

function CreateAgent({
  payload,
  copying,
  onCreated,
}: {
  payload: AdminPayload;
  copying: Agent | null;
  onCreated: (name: string, spec: Record<string, unknown>) => void;
}) {
  const [name, setName] = useState("");
  const [model, setModel] = useState(copying?.model ?? payload.models[0]);
  const [reasoning, setReasoning] = useState(payload.reasoning_levels[0]);
  const [internet, setInternet] = useState(copying ? copying.internet_access_allowed : true);
  const [prompt, setPrompt] = useState("");

  useEffect(() => {
    if (!copying) return;
    setModel(copying.model);
    setInternet(copying.internet_access_allowed);
    let live = true;
    getJson<{ agent: { prompt: string }; spec: { reasoning: string } }>(
      "/agents/" + copying.id + "/overview",
    ).then((result) => {
      if (!live || !result.ok) return;
      setPrompt(result.payload.agent.prompt);
      setReasoning(result.payload.spec.reasoning);
    });
    return () => {
      live = false;
    };
  }, [copying]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || !prompt.trim()) return;
    onCreated(name.trim(), {
      model,
      internet_access_allowed: internet,
      reasoning,
      prompt,
    });
  }

  return (
    <div>
      <h2 className="m-0 text-section">Create agent</h2>
      <form onSubmit={submit} className="flex max-w-form flex-col gap-sm">
        <Input
          placeholder="agent name"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <Select value={model} onChange={(event) => setModel(event.target.value)}>
          {payload.models.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </Select>
        <Select value={reasoning} onChange={(event) => setReasoning(event.target.value)}>
          {payload.reasoning_levels.map((level) => (
            <option key={level} value={level}>
              {level}
            </option>
          ))}
        </Select>
        <label>
          <Checkbox
            checked={internet}
            onChange={(event) => setInternet(event.target.checked)}
          />{" "}
          public internet
        </label>
        <Textarea
          placeholder="System prompt"
          rows={4}
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
        />
        <Button type="submit" variant="send" className="self-start">
          Create
        </Button>
      </form>
    </div>
  );
}
