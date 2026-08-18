import { type FormEvent, useRef, useState } from "react";

import { Button, ConfirmButton } from "@/components/ui/button";
import { Facts } from "@/components/ui/facts";
import { Field, Hint, Input } from "@/components/ui/field";
import { ACTS, Td, TdActs, TdFact } from "@/components/ui/table";
import { useBeside } from "@/kernel/beside";
import {
  type NoticeState,
  OutcomeNotice,
  Panel,
  PanelEmpty,
  PanelSkeleton,
  QUIET,
  Section,
  outcomeNotice,
  usePanelRead,
} from "@/kernel/panel";
import { Page, PageHeader, Pane, RecordPanel } from "@/kernel/pane";
import { DataTable, OPEN } from "@/kernel/table";
import { agentName } from "@/lib/agentName";
import { postIntent } from "@/lib/api";
import { surfaceWord, webAudienceLabel } from "@/lib/audience";
import { money } from "@/lib/money";
import type { AdminAgent, AdminPayload, Member } from "@/lib/types";

/** The row states what the eye compares down the column and hands the rest to the record beside it:
 *  an agent's surfaces are a list of unknown length, and the act that grants web access is a field
 *  and two buttons, which no fixed track holds at the pitch a row is read at. */
const AGENT_COLUMNS = [
  "Agent",
  { label: "Model", fact: true },
  { label: "Public Internet", fact: true },
  "Web Audience",
];
const MEMBER_COLUMNS = [
  "Member",
  { label: "Role", fact: true },
  { label: "Seat", fact: true },
  "",
];
const CAP_COLUMNS = [
  { label: "Cap", fact: true },
  "Subject",
  { label: "Window", fact: true },
  { label: "Limit", fact: true },
  { label: "On Breach", fact: true },
];
const EXTENSION_COLUMNS = [
  "Extension",
  { label: "Version", fact: true },
  { label: "Public Internet", fact: true },
];

function allowance(allowed: boolean): string {
  return allowed ? "Allowed" : "Blocked";
}

export function Admin() {
  const [reloads, setReloads] = useState(0);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const state = usePanelRead<AdminPayload>("/api/admin", reloads);

  async function intent(agentId: string, envelope: unknown): Promise<NoticeState> {
    const outcome = await postIntent(agentId, envelope);
    if (outcome.applied) setReloads((count) => count + 1);
    return outcomeNotice(outcome);
  }

  return (
    <Panel
      state={state}
      loading={() => (
        <Pane data-testid="admin-loading">
          <Page>
            <PanelSkeleton shape="table" />
          </Page>
        </Pane>
      )}
      failed={(message) => (
        <Pane>
          <Page>
            <PanelEmpty>{message}</PanelEmpty>
          </Page>
        </Pane>
      )}
    >
      {(payload) => {
        const mainAgent = payload.agents.find((agent) => agent.main);

        return (
          <Pane data-testid="admin">
            <Page>
              <PageHeader title="Administration" />
              <OutcomeNotice state={notice} />

              <AgentSection agents={payload.agents} onIntent={intent} />

              <Section title="Members">
                <DataTable
                  columns={MEMBER_COLUMNS}
                  rows={payload.members}
                  rowKey={(member) => member.email}
                  empty="This workspace has no members yet."
                >
                  {(member) => (
                    <MemberRow
                      member={member}
                      onApply={async (spec) => {
                        if (!mainAgent) return;
                        setNotice(
                          await intent(mainAgent.id, {
                            verb: "apply",
                            kind: "member",
                            name: member.id,
                            spec,
                          }),
                        );
                      }}
                    />
                  )}
                </DataTable>
              </Section>

              <Section title="Billing">
                <DataTable
                  columns={CAP_COLUMNS}
                  rows={payload.caps}
                  rowKey={(cap) => cap.scope + "/" + cap.subject + "/" + cap.window_seconds}
                  empty="No spend caps are set."
                >
                  {(cap) => (
                    <>
                      <TdFact>{cap.scope}</TdFact>
                      <Td>{cap.subject || "—"}</Td>
                      <TdFact>{cap.window_seconds / 3600 + "h"}</TdFact>
                      <TdFact>{money(cap.limit_micro_usd)}</TdFact>
                      <TdFact>{cap.on_breach}</TdFact>
                    </>
                  )}
                </DataTable>
                <Hint className="m-0">
                  The plan, invoices, and payment methods are managed with the agent in chat.
                </Hint>
              </Section>

              <Section title="Deploy">
                <Facts
                  rows={[
                    {
                      label: "Sandbox public internet",
                      value: allowance(payload.deploy.sandbox_internet),
                    },
                  ]}
                />
                <DataTable
                  columns={EXTENSION_COLUMNS}
                  rows={payload.deploy.extensions}
                  rowKey={(extension) => extension.name}
                  empty="This deploy installs no extensions."
                >
                  {(extension) => (
                    <>
                      <Td>{extension.name}</Td>
                      <TdFact>{extension.version}</TdFact>
                      <TdFact>{allowance(extension.sandbox_internet)}</TdFact>
                    </>
                  )}
                </DataTable>
              </Section>
            </Page>
          </Pane>
        );
      }}
    </Panel>
  );
}

/** The agents, and the one of them the member opened. The record stands in the pane's second
 *  column, so the acts on an agent are taken beside the row rather than inside it. */
function AgentSection({
  agents,
  onIntent,
}: {
  agents: AdminAgent[];
  onIntent: (agentId: string, envelope: unknown) => Promise<NoticeState>;
}) {
  const [opened, setOpened] = useState<string | null>(null);
  const agent = agents.find((row) => row.id === opened);
  const beside = useBeside(
    agent ? (
      <AgentRecord agent={agent} onClose={() => setOpened(null)} onIntent={onIntent} />
    ) : null,
    () => setOpened(null),
  );

  return (
    <>
      <Section title="Agents">
        <DataTable
          columns={AGENT_COLUMNS}
          rows={agents}
          rowKey={(row) => row.id}
          empty="This workspace has no agents."
          open={(row) => () => setOpened(row.id)}
          act={() => OPEN}
        >
          {(row) => (
            <>
              <Td>
                {agentName(row.name)}
                {row.main ? <span className="ml-xs text-small text-ink-soft">Main</span> : null}
              </Td>
              <TdFact>{row.model}</TdFact>
              <TdFact>{allowance(row.internet_access_allowed)}</TdFact>
              <Td>{webAudienceLabel(row.main, row.web_audience)}</Td>
            </>
          )}
        </DataTable>
      </Section>
      {beside}
    </>
  );
}

/** One agent: what it is, and who reaches it on the web. The main agent answers every member, so it
 *  carries no grant form — an audience that is already everyone is not one an address adds to.
 *
 *  Granting and revoking read the one field, so both are held to the one contract that field
 *  declares: the form validates before either posts, and an address it refuses is refused on the
 *  field itself, where the member is looking. A press the handler drops instead is a live control
 *  that answers nothing. */
function AgentRecord({
  agent,
  onClose,
  onIntent,
}: {
  agent: AdminAgent;
  onClose: () => void;
  onIntent: (agentId: string, envelope: unknown) => Promise<NoticeState>;
}) {
  const form = useRef<HTMLFormElement>(null);
  const [email, setEmail] = useState("");
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [busy, setBusy] = useState(false);

  async function audience(verb: string) {
    if (busy || !form.current?.reportValidity()) return;
    setBusy(true);
    setNotice(await onIntent(agent.id, { verb, email }));
    setBusy(false);
  }

  return (
    <RecordPanel title={agentName(agent.name)} onClose={onClose}>
      <Facts
        rows={[
          { label: "Model", value: agent.model },
          { label: "Public Internet", value: allowance(agent.internet_access_allowed) },
          { label: "Surfaces", value: agent.installations.map(surfaceWord).join(", ") || "—" },
          { label: "Web Audience", value: webAudienceLabel(agent.main, agent.web_audience) },
        ]}
      />
      {agent.main ? null : (
        <form
          ref={form}
          onSubmit={(event: FormEvent) => {
            event.preventDefault();
            audience("grant_web_access");
          }}
          className="flex flex-col gap-xl"
        >
          <OutcomeNotice state={notice} />
          <Field label="Web Access Address" htmlFor="web-access">
            <Input
              id="web-access"
              type="email"
              required
              placeholder="email@work.com"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </Field>
          <div className="flex justify-end gap-sm">
            <ConfirmButton
              verb="Revoke"
              size="bar"
              busy={busy}
              onClick={() => audience("revoke_web_access")}
            />
            <Button type="submit" variant="send" size="bar" busy={busy}>
              Grant
            </Button>
          </div>
        </form>
      )}
    </RecordPanel>
  );
}

function MemberRow({
  member,
  onApply,
}: {
  member: Member;
  onApply: (spec: { admin: boolean; seated: boolean }) => void;
}) {
  return (
    <>
      <Td>{member.email}</Td>
      <TdFact>{member.admin ? "Admin" : "Member"}</TdFact>
      <TdFact>{member.seated ? "Seated" : "Unseated"}</TdFact>
      <TdActs>
        <div className={ACTS}>
          {member.admin ? (
            <ConfirmButton
              verb="Remove admin"
              variant="row"
              onClick={() => onApply({ admin: false, seated: Boolean(member.seated) })}
            />
          ) : (
            <Button
              variant="row"
              onClick={() => onApply({ admin: true, seated: Boolean(member.seated) })}
            >
              Make admin
            </Button>
          )}
          {member.seated ? (
            <ConfirmButton
              verb="Unseat"
              variant="row"
              onClick={() => onApply({ admin: member.admin, seated: false })}
            />
          ) : (
            <Button variant="row" onClick={() => onApply({ admin: member.admin, seated: true })}>
              Seat
            </Button>
          )}
        </div>
      </TdActs>
    </>
  );
}
