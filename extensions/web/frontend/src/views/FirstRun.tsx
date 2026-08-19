import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { Page, PageHeader, Pane } from "@/kernel/pane";
import {
  Notice,
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
import { postIntent } from "@/lib/api";
import { setPendingAsk } from "@/lib/pendingAsk";
import { ProviderGlyph } from "@/lib/providerGlyph";
import type { Agent, Member } from "@/lib/types";

type ProviderTile = { name: string; label: string };

type Connector = ProviderTile & { installed: boolean };

type FirstRunPayload = {
  providers: ProviderTile[];
  connectors: Connector[];
};

/** What stands in the composer once the first run hands the member over: the tools the member just
 *  picked, and the question the page stops at. The picks are written into the message in catalog
 *  order rather than left to a memory read, so the agent proposes against them on its first turn
 *  without asking again for what the member already answered. Which agents to set up is that
 *  conversation's work: creating one takes a speaking member, so it happens on the turns the member
 *  answers on, never on a page that never speaks. */
function opening(labels: string[]): string {
  const ask = "What could you set up for us?";
  return labels.length ? "We use " + labels.join(", ") + ". " + ask : ask;
}

/** The intent each connect step submits. The named tool mints the install link inside the turn and
 *  the outcome carries it back, so installing takes no message the member has to send. */
const CONNECT_VERB: Record<string, string> = {
  slack: "connect_slack",
  github: "connect_github",
};

/** What the workspace gains by installing each one, stated where the act is. */
const CONNECT_NOTE: Record<string, string> = {
  slack: "The agent answers mentions and direct messages in your Slack workspace.",
  github: "The agent reads and pushes to the repositories the installation grants.",
};

const TOOLS_STEP = "tools";
const TEAM_STEP = "team";

/** The first run, one step at a time: the member states what their team uses, and that pick decides
 *  what the page asks next. Picking a connector reveals its install step, so the two connectors
 *  that put the agent where the team already works are asked for in place, by the member who just
 *  named them; picking neither leaves the page asking only who else belongs in the workspace.
 *  One step stands at a time: an answered step leaves the page, so what is on screen is always the
 *  one decision being asked for. The last act hands the opening message to the main agent's new
 *  chat unsent, and the member sends it. */
export function FirstRun({
  agent,
  member,
  onOpenChat,
}: {
  agent: Agent;
  member: Member;
  onOpenChat: () => void;
}) {
  const state = usePanelRead<FirstRunPayload>("/workspace/first-run");
  const [picked, setPicked] = useState<string[]>([]);
  const [recorded, setRecorded] = useState<string[] | null>(null);
  const [at, setAt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<NoticeState>(QUIET);

  function toggle(name: string) {
    setPicked((held) =>
      held.includes(name) ? held.filter((entry) => entry !== name) : [...held, name],
    );
  }

  async function record() {
    if (busy) return;
    if (picked.length) {
      setBusy(true);
      const outcome = await postIntent(agent.id, {
        verb: "record_tooling",
        kind: "memory",
        providers: picked,
      });
      setBusy(false);
      if (!outcome.applied) {
        setNotice(outcomeNotice(outcome));
        return;
      }
    }
    setNotice(QUIET);
    setRecorded(picked);
    setAt(1);
  }

  return (
    <Panel
      state={state}
      loading={() => (
        <Pane>
          <Page>
            <PanelSkeleton shape="form" />
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
        const revealed =
          recorded === null
            ? [TOOLS_STEP]
            : [
                TOOLS_STEP,
                ...payload.connectors
                  .filter((row) => recorded.includes(row.name))
                  .map((row) => row.name),
                TEAM_STEP,
              ];
        const advance = () => {
          if (at + 1 < revealed.length) {
            setAt(at + 1);
            return;
          }
          setPendingAsk(
            agent.id,
            opening(
              payload.providers
                .filter((tile) => (recorded ?? []).includes(tile.name))
                .map((tile) => tile.label),
            ),
            false,
          );
          onOpenChat();
        };
        return (
          <Pane>
            <Page>
              <PageHeader title="Set up this workspace" />
              <OutcomeNotice state={notice} />

              {[revealed[at]].map((step) => {
                const connector = payload.connectors.filter((row) => row.name === step)[0];
                return (
                  <div key={step} className="flex flex-col gap-md">
                    {step === TOOLS_STEP ? (
                      <Section
                        title="What your team uses"
                        note="Picks are recorded in memory, and the agent reads them on every later turn."
                      >
                        <ul className="m-0 grid list-none grid-cols-4 gap-2xs p-0 max-narrow:grid-cols-2">
                          {payload.providers.map((tile) => (
                            <li key={tile.name}>
                              <Button
                                variant="option"
                                className="size-full flex-col gap-2xs px-sm py-md"
                                aria-pressed={picked.includes(tile.name)}
                                disabled={recorded !== null}
                                onClick={() => toggle(tile.name)}
                              >
                                <ProviderGlyph provider={tile.name} className="text-inherit" />
                                {tile.label}
                              </Button>
                            </li>
                          ))}
                        </ul>
                      </Section>
                    ) : null}
                    {connector ? (
                      <Connect agent={agent} admin={member.admin} row={connector} />
                    ) : null}
                    {step === TEAM_STEP ? <Invite agent={agent} admin={member.admin} /> : null}
                    <div>
                      <Button busy={busy} onClick={step === TOOLS_STEP ? record : advance}>
                        Continue
                      </Button>
                    </div>
                  </div>
                );
              })}
            </Page>
          </Pane>
        );
      }}
    </Panel>
  );
}

/** One connector's install step. The intent dispatches that connector's own admin-gated tool, which
 *  seals the install link for this workspace and answers with it — a non-admin is told who installs
 *  it rather than pressing an act the workspace refuses. The link expires, so the act stays on the
 *  page and mints another. */
function Connect({ agent, admin, row }: { agent: Agent; admin: boolean; row: Connector }) {
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState<string | null>(null);
  const [notice, setNotice] = useState<NoticeState>(QUIET);

  async function connect() {
    if (busy) return;
    setBusy(true);
    const outcome = await postIntent(agent.id, { verb: CONNECT_VERB[row.name] });
    setBusy(false);
    setLink(outcome.url ?? null);
    setNotice(outcome.url ? QUIET : outcomeNotice(outcome));
  }

  return (
    <Section title={"Connect " + row.label} note={CONNECT_NOTE[row.name]}>
      <div className="flex items-center gap-md">
        {row.installed ? (
          <span className="flex items-center gap-sm text-label text-ink-soft">
            <ProviderGlyph provider={row.name} />
            Connected
          </span>
        ) : admin ? (
          <Button variant="send" busy={busy} onClick={connect}>
            <ProviderGlyph provider={row.name} className="text-inherit" />
            {"Connect " + row.label}
          </Button>
        ) : (
          <span className="text-label text-ink-soft">
            {"A workspace admin connects " + row.label + "."}
          </span>
        )}
      </div>
      {link ? (
        <Notice>
          <a href={link} target="_blank" rel="noopener">
            {"Open the " + row.label + " install page"}
          </a>
        </Notice>
      ) : null}
      <OutcomeNotice state={notice} />
    </Section>
  );
}

/** The last step: teammates added by address, one intent each, through the same admin-only verb
 *  chat adds a member with. Nothing reaches the address — the member exists once the verb applies,
 *  and they reach the workspace by signing in. Each one is stated as it lands, so the member reads
 *  what they added before the page hands them on. */
function Invite({ agent, admin }: { agent: Agent; admin: boolean }) {
  const [email, setEmail] = useState("");
  const [added, setAdded] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<NoticeState>(QUIET);

  async function add(event: FormEvent) {
    event.preventDefault();
    const wanted = email.trim();
    if (busy || !wanted) return;
    setBusy(true);
    const outcome = await postIntent(agent.id, { verb: "add_member", email: wanted, admin: false });
    setBusy(false);
    if (!outcome.applied) {
      setNotice(outcomeNotice(outcome));
      return;
    }
    setNotice(QUIET);
    setAdded((held) => [...held, wanted]);
    setEmail("");
  }

  return (
    <Section
      title="Invite your team"
      note="Each address becomes a member of this workspace. They sign in with their work email."
    >
      {admin ? (
        <form onSubmit={add} className="flex items-stretch gap-sm">
          <Input
            type="email"
            required
            aria-label="Email"
            placeholder="email@work.com"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
          <Button type="submit" variant="send" busy={busy}>
            Add
          </Button>
        </form>
      ) : (
        <p className="m-0 text-label text-ink-soft">A workspace admin adds members.</p>
      )}
      {added.length ? (
        <Notice>
          {(added.length === 1 ? "Added " : "Added " + added.length + " members: ") +
            added.join(", ") +
            "."}
        </Notice>
      ) : null}
      <OutcomeNotice state={notice} />
    </Section>
  );
}
