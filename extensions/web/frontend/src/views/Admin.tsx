import { useState } from "react";

import { Button, ConfirmButton } from "@/components/ui/button";
import { Facts } from "@/components/ui/facts";
import { Field, Hint } from "@/components/ui/field";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Sheet } from "@/components/ui/sheet";
import { ACTS, Td, TdActs, TdFact } from "@/components/ui/table";
import { ActionControls } from "@/kernel/action";
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
import { Header, Page, Pane } from "@/kernel/pane";
import { closed, opened } from "@/kernel/slots";
import { DataTable, OPEN } from "@/kernel/table";
import { agentName } from "@/lib/agentName";
import { postAction, postIntent, type IntentOutcome } from "@/lib/api";
import { surfaceWord, webAudienceLabel } from "@/lib/audience";
import { money } from "@/lib/money";
import type { ActionInput, ActionView, AdminAgent, AdminPayload, Member } from "@/lib/types";
import { MEMBER_COLUMNS, MemberCells } from "@/views/Team";

const AGENT_COLUMNS = [
  "App",
  { label: "Model", fact: true },
  { label: "Public Internet", fact: true },
  "Web Audience",
];
const ADMIN_MEMBER_COLUMNS = [...MEMBER_COLUMNS, ""];
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

const APP = "agent/";

function allowance(allowed: boolean): string {
  return allowed ? "Allowed" : "Blocked";
}

export function Admin() {
  const [reloads, setReloads] = useState(0);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const state = usePanelRead<AdminPayload>("/api/admin", reloads);

  function settled(outcome: IntentOutcome): IntentOutcome {
    if (outcome.applied) setReloads((count) => count + 1);
    return outcome;
  }

  async function intent(agentId: string, envelope: unknown): Promise<IntentOutcome> {
    return settled(await postIntent(agentId, envelope));
  }

  async function action(
    agentId: string,
    view: ActionView,
    input: ActionInput,
  ): Promise<IntentOutcome> {
    return settled(await postAction(agentId, view.call, input));
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
              <Header heading={1} title="Administration" />
              <OutcomeNotice state={notice} />

              <AgentSection agents={payload.agents} members={payload.members} onAction={action} />

              <Section title="Members">
                <DataTable
                  columns={ADMIN_MEMBER_COLUMNS}
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
                          outcomeNotice(
                            await intent(mainAgent.id, {
                              verb: "apply",
                              kind: "member",
                              name: member.id,
                              spec,
                            }),
                          ),
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
                  The plan, invoices, and payment methods are managed with the app in chat.
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

function AgentSection({
  agents,
  members,
  onAction,
}: {
  agents: AdminAgent[];
  members: Member[];
  onAction: (agentId: string, view: ActionView, input: ActionInput) => Promise<IntentOutcome>;
}) {
  const [opens, setOpens] = useState<string[]>([]);
  const sheet = opens.slice(-1).map((id) => {
    const agent = agents.find((row) => APP + row.id === id);
    if (!agent) return null;
    return (
      <AgentRecord
        key={id}
        agent={agent}
        members={members}
        onClose={() => setOpens((held) => closed(held, id))}
        onAction={onAction}
      />
    );
  });

  return (
    <>
      <Section title="Apps">
        <DataTable
          columns={AGENT_COLUMNS}
          rows={agents}
          rowKey={(row) => row.id}
          empty="This workspace has no apps."
          open={(row) => () => setOpens((held) => opened(held, APP + row.id))}
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
      {sheet}
    </>
  );
}

/** One agent: what it is, and who reaches it on the web. The main agent answers every member, so it
 *  carries no audience acts — an audience that is already everyone is not one a member adds to.
 *
 *  The acts are the member object's own, projected on each member row the administration read
 *  carries: the admin picks the member, and the grant and the revoke draw from their declarations,
 *  posted on this agent's lane so the audience they change is this agent's. */
function AgentRecord({
  agent,
  members,
  onClose,
  onAction,
}: {
  agent: AdminAgent;
  members: Member[];
  onClose: () => void;
  onAction: (agentId: string, view: ActionView, input: ActionInput) => Promise<IntentOutcome>;
}) {
  const [picked, setPicked] = useState("");
  const member = members.find((entry) => entry.id === picked);

  return (
    <Sheet open title={agentName(agent.name)} onClose={onClose}>
      <Facts
        rows={[
          { label: "Model", value: agent.model },
          { label: "Public Internet", value: allowance(agent.internet_access_allowed) },
          { label: "Surfaces", value: agent.installations.map(surfaceWord).join(", ") || "—" },
          { label: "Web Audience", value: webAudienceLabel(agent.main, agent.web_audience) },
        ]}
      />
      {agent.main ? null : (
        <div className="flex flex-col gap-xl">
          <Field label="Web Access Member" htmlFor="web-access-member">
            <Select value={picked} onValueChange={setPicked}>
              <SelectTrigger id="web-access-member">
                <SelectValue placeholder="Choose a member" />
              </SelectTrigger>
              <SelectContent>
                {members.map((entry) => (
                  <SelectItem key={entry.email} value={entry.id ?? entry.email}>
                    {entry.email}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          {member ? (
            <ActionControls
              key={member.id}
              views={member.actions ?? []}
              post={(view, input) => onAction(agent.id, view, input)}
            />
          ) : null}
        </div>
      )}
    </Sheet>
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
      <MemberCells member={member} />
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
