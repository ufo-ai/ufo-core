import { IconArrowRight, IconCheck, IconPlus } from "@tabler/icons-react";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  Notice,
  type NoticeState,
  OutcomeNotice,
  Panel,
  PanelEmpty,
  PanelSkeleton,
  QUIET,
  outcomeNotice,
  usePanelRead,
} from "@/kernel/panel";
import logo from "@/assets/ufo-logo.svg";
import { postIntent } from "@/lib/api";
import { BrandMark } from "@/lib/brandMark";
import { cn } from "@/lib/cn";
import { setPendingAsk } from "@/lib/pendingAsk";
import type { Agent, Member } from "@/lib/types";

type ProviderTile = { name: string; label: string };

type Connector = ProviderTile & { installed: boolean };

type FirstRunPayload = {
  providers: ProviderTile[];
  connectors: Connector[];
};

/** What the member says to open the conversation the first run hands them to: the tools they just
 *  picked, and the question the page stops at. The picks are written into the message in catalog
 *  order rather than left to a memory read, so the agent proposes against them on its first turn
 *  without asking again for what the member already answered. Which agents to set up is that
 *  conversation's work: creating one takes a speaking member, so it happens on the turns the member
 *  answers on, never on a page that never speaks.
 *
 *  It is said as the chat opens rather than left standing in the composer. The line commits
 *  nothing — it asks what the agent could do, and every act that follows is decided on a later
 *  turn the member answers — so a member who has just pressed through the whole setup arrives at a
 *  reply rather than at a box holding words they have to press again. */
function opening(labels: string[]): string {
  const ask = "What could you set up for us?";
  return labels.length ? "We use " + labels.join(", ") + ". " + ask : ask;
}

/** The first run's own read. The page reads it for the steps it draws, and a connect step waiting
 *  on an install reads the same one again for the single fact it is waiting on. */
const FIRST_RUN_READ = "/workspace/first-run";

/** How often a standing connect step re-reads while it waits. The install happens on the provider's
 *  own pages, so nothing on this page can say when it lands — the wait is the member's, and it is
 *  measured in the seconds they spend over there rather than in the half-minute a pane showing
 *  records can hold a stale answer for. */
const WATCH_MS = 3_000;

/** The intent each connect step submits. The named tool mints the install link inside the turn and
 *  the outcome carries it back, so installing takes no message the member has to send. */
const CONNECT_VERB: Record<string, string> = {
  slack: "connect_slack",
  github: "connect_github",
};

/** Each connector gets the whole step, and the step states what the workspace gains rather than
 *  naming the act twice: the button under it already says Connect, so a heading that said it too
 *  would ask the member to press a word they have just read. What they are deciding is whether they
 *  want the agent in that product at all, so that is what the step says. */
const CONNECT_COPY: Record<string, { title: string; note: string }> = {
  slack: {
    title: "The app answers in Slack",
    note: "Mention it in a channel or send it a direct message, and it replies where your team already works.",
  },
  github: {
    title: "The app works in your repositories",
    note: "It reads the code and pushes to the repositories the installation grants. You pick which ones.",
  },
};

const TOOLS_STEP = "tools";
const TEAM_STEP = "team";

/** The invite step's form, named so the act that submits it can stand in the page's head with the
 *  other acts rather than inside the fields it commits. */
const INVITE_FORM = "first-run-invite";

/** Blank addresses the invite step opens with. A team is more than one person, so the step asks as
 *  though several are coming: one box states that a teammate is an afterthought, and a member with
 *  more to add says so with `Add another`. */
const INVITE_ROWS = 3;

/** What each step asks, keyed by the step's own name — which for a connector is the connector's
 *  slug, so a step and the copy over it cannot drift apart. */
const STEP_COPY: Record<string, { title: string; note: string }> = {
  [TOOLS_STEP]: {
    title: "What your team uses",
    note: "Picks are recorded in memory, and the app reads them on every later turn.",
  },
  [TEAM_STEP]: {
    title: "Invite your team",
    note: "Each address becomes a member of this workspace. They sign in with their work email.",
  },
  ...CONNECT_COPY,
};

/** The page the first run is read on, and the only screen in the portal that draws no navigation:
 *  a member who has not set the workspace up has nowhere to navigate to yet, and a bar offering
 *  four destinations invites them to leave the one thing they are here to finish. The mark and the
 *  step's acts share that line instead, at the two edges of the page — so the acts stand where the
 *  bar's own do, and the step below is only its question and the answer being given to it.
 *
 *  The question is centred over that answer, which is what makes it a question rather than the
 *  heading of a screen: nothing is aligned to it, because there is nothing else on the line. */
function Frame({
  title,
  note,
  actions,
  children,
}: {
  title?: string;
  note?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <main
      className={cn(
        "grid h-dvh grid-rows-[auto_1fr] gap-6xl overflow-y-auto",
        "px-(--size-page-gutter) py-(--size-page-top) max-narrow:px-2xl",
      )}
    >
      <div className="flex items-center gap-sm">
        <span
          role="img"
          aria-label="ufo"
          className="block h-(--size-wordmark) w-(--size-logo) shrink-0 bg-current"
          style={{ mask: `url(${logo}) center / contain no-repeat` }}
        />
        <div className="ml-auto flex items-center gap-sm">{actions}</div>
      </div>
      <div className="mx-auto flex w-full max-w-section flex-col justify-center gap-6xl">
        {title ? (
          <div className="flex flex-col gap-2xs text-center">
            <h1 className="m-0 text-title font-medium">{title}</h1>
            {note ? <p className="m-0 mx-auto max-w-form text-body text-ink-soft">{note}</p> : null}
          </div>
        ) : null}
        {children}
      </div>
    </main>
  );
}

/** The first run, one step at a time: the member states what their team uses, and that pick decides
 *  what the page asks next. Picking a connector reveals its install step, so the two connectors
 *  that put the agent where the team already works are asked for in place, by the member who just
 *  named them; picking neither leaves the page asking only who else belongs in the workspace.
 *  One step stands at a time: an answered step leaves the page, so what is on screen is always the
 *  one decision being asked for. The last act opens the main agent's new chat on the opening
 *  message, said.
 *
 *  The head states the acts the step actually has. Back is absent on the first step, because there
 *  is nothing behind it. Continue commits the step and is held closed until the step's act is
 *  done — so a step that asks for an install says plainly that it is still waiting — and every
 *  step that can be left undone carries Skip beside it, because a closed Continue with no way past
 *  it is a dead end rather than a question.
 *
 *  An install lands on the provider's pages, not this one, so a connect step watches for it and
 *  passes itself when it arrives: the member finishes over there, comes back, and reads the next
 *  question rather than a step they already answered with a Continue standing under it. A connector
 *  the workspace already held when the step opened is not that — the member never left, so it
 *  states it is connected and waits to be read. */
export function FirstRun({
  agent,
  member,
  onOpenChat,
}: {
  agent: Agent;
  member: Member;
  onOpenChat: () => void;
}) {
  const state = usePanelRead<FirstRunPayload>(FIRST_RUN_READ);
  const [picked, setPicked] = useState<string[]>([]);
  const [recorded, setRecorded] = useState<string[] | null>(null);
  const [connected, setConnected] = useState<string[]>([]);
  const [rows, setRows] = useState<string[]>(() => Array<string>(INVITE_ROWS).fill(""));
  const [added, setAdded] = useState<string[]>([]);
  const [at, setAt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<NoticeState>(QUIET);

  /** The picks reach memory once per answer rather than once per visit: a member who steps back to
   *  change them writes again, and one who steps back and changes nothing does not. The write takes
   *  at least one pick, so clearing every pick after recording leaves the earlier answer standing —
   *  correcting that is a sentence to the agent, which is where memory is corrected everywhere
   *  else. */
  async function record() {
    if (busy) return;
    const stated = [...picked].sort().join(" ");
    const written = recorded === null ? null : [...recorded].sort().join(" ");
    if (picked.length && stated !== written) {
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
        <Frame>
          <PanelSkeleton shape="form" />
        </Frame>
      )}
      failed={(message) => (
        <Frame>
          <PanelEmpty>{message}</PanelEmpty>
        </Frame>
      )}
    >
      {(payload) => {
        const chosen = payload.connectors.filter((row) => (recorded ?? []).includes(row.name));
        const revealed =
          recorded === null
            ? [TOOLS_STEP]
            : [TOOLS_STEP, ...chosen.map((row) => row.name), TEAM_STEP];
        const step = revealed[at];
        const connector = chosen.filter((row) => row.name === step)[0];
        const held = connector ? connector.installed || connected.includes(step) : false;
        const finish = () => {
          setPendingAsk(
            agent.id,
            opening(
              payload.providers
                .filter((tile) => (recorded ?? []).includes(tile.name))
                .map((tile) => tile.label),
            ),
            true,
          );
          onOpenChat();
        };
        const advance = () => (at + 1 < revealed.length ? setAt(at + 1) : finish());
        const wanted = rows
          .map((row) => row.trim())
          .filter(Boolean)
          .filter((email) => !added.includes(email));
        /** Every address the member wrote, one intent each, and then the chat. A refusal stops the
         *  run where it happened and states itself: the addresses already taken are held, so
         *  pressing again asks only for the ones that never landed. */
        const invite = async (event: FormEvent) => {
          event.preventDefault();
          if (busy) return;
          setBusy(true);
          for (const email of wanted) {
            const outcome = await postIntent(agent.id, { verb: "add_member", email, admin: false });
            if (!outcome.applied) {
              setBusy(false);
              setNotice(outcomeNotice(outcome));
              return;
            }
            setAdded((held) => [...held, email]);
          }
          setBusy(false);
          finish();
        };
        return (
          <Frame
            title={STEP_COPY[step].title}
            note={STEP_COPY[step].note}
            actions={
              <>
                {at > 0 ? (
                  <Button size="bar" onClick={() => setAt(at - 1)}>
                    Back
                  </Button>
                ) : null}
                {step === TOOLS_STEP ? null : (
                  <Button size="bar" onClick={advance}>
                    Skip
                  </Button>
                )}
                {step === TEAM_STEP ? (
                  <Button
                    type="submit"
                    form={INVITE_FORM}
                    variant="send"
                    size="bar"
                    busy={busy}
                    disabled={!wanted.length}
                  >
                    Invite
                  </Button>
                ) : (
                  <Button
                    variant="send"
                    size="bar"
                    busy={busy}
                    disabled={connector !== undefined && !held}
                    onClick={step === TOOLS_STEP ? record : advance}
                  >
                    Continue
                  </Button>
                )}
              </>
            }
          >
            <OutcomeNotice state={notice} />
            {step === TOOLS_STEP ? (
              <ToggleGroup
                className="flex flex-wrap justify-center gap-lg"
                value={picked}
                onValueChange={setPicked}
              >
                {payload.providers.map((tile) => (
                  <Tile key={tile.name} name={tile.name} label={tile.label} />
                ))}
              </ToggleGroup>
            ) : null}
            {connector ? (
              <Connect
                key={connector.name}
                agent={agent}
                admin={member.admin}
                row={connector}
                held={held}
                onConnected={() => {
                  setConnected((names) => [...names, connector.name]);
                  advance();
                }}
              />
            ) : null}
            {step === TEAM_STEP ? (
              <Invite
                admin={member.admin}
                rows={rows}
                added={added}
                onRows={setRows}
                onSubmit={invite}
              />
            ) : null}
          </Frame>
        );
      }}
    </Panel>
  );
}

/** One provider the team can say it uses: its own mark on a card, and the name under the card. The
 *  mark is what the member scans for, so it is drawn at the size of a thing being looked for and in
 *  the product's own colours, and the label only confirms what the mark already said.
 *
 *  Every card carries the slot its pick lands in, empty until it is picked. Marks this saturated
 *  leave a tinted border with nothing to say — a colour among fourteen colours — so what separates
 *  a picked tile from an unpicked one is a shape that was already on screen, filling. */
function Tile({ name, label }: { name: string; label: string }) {
  return (
    <ToggleGroupItem
      value={name}
      className="group flex w-(--size-app-tile) flex-col items-center gap-sm"
    >
      <span
        className={cn(
          "relative flex aspect-square w-full items-center justify-center",
          "rounded-panel border border-edge bg-surface",
          "group-hover:border-edge-strong group-aria-pressed:border-ink",
        )}
      >
        <BrandMark provider={name} />
        <span
          className={cn(
            "absolute top-xs right-xs flex size-(--size-glyph) items-center justify-center",
            "rounded-full border border-edge transition-colors duration-100 ease-control",
            "group-aria-pressed:border-ink group-aria-pressed:bg-ink",
          )}
        >
          <IconCheck
            className="size-icon text-surface opacity-0 group-aria-pressed:opacity-100"
            aria-hidden
          />
        </span>
      </span>
      <span className="text-label text-ink-soft group-aria-pressed:text-ink">{label}</span>
    </ToggleGroupItem>
  );
}

/** Waits for the install this step asked for, and reports it once. The install is granted on the
 *  provider's pages, which tell this page nothing, so the only account of it is the projection the
 *  page already reads — asked for often while a step waits on it, and once more the moment the tab
 *  carrying that step is looked at again, which is what a member coming back from the install is
 *  doing.
 *
 *  `watching` is what arms it, and a step that opened on a connector the workspace already held
 *  never arms: it reads nothing, and reports nothing to advance past. So the report is only ever
 *  the install arriving under a member who was waiting for it, which is the one thing that should
 *  move them on. */
function useConnected(name: string, watching: boolean, onConnected: () => void) {
  const state = usePanelRead<FirstRunPayload>(watching ? FIRST_RUN_READ : null, 0, WATCH_MS);
  const landed =
    state.phase === "ready" &&
    state.payload.connectors.some((row) => row.name === name && row.installed);
  const reported = useRef(false);
  useEffect(() => {
    if (!landed || reported.current) return;
    reported.current = true;
    onConnected();
  }, [landed, onConnected]);
}

/** One connector's own step. The intent dispatches that connector's admin-gated tool, which seals
 *  the install link for this workspace and answers with it — a non-admin is told who installs it
 *  rather than pressing an act the workspace refuses. The link expires, so the act stays on the
 *  step and mints another.
 *
 *  Each connector gets a step to itself because they are two different decisions, not two rows of
 *  one: putting the agent in the team's chat and giving it the team's code are worth different
 *  amounts to different teams, and a member reading a list weighs them against each other instead
 *  of against their own work. The step's heading carries what the connector is worth, and the act
 *  under it is the only thing on the screen — it carries the mark itself, so the product is named
 *  once rather than drawn twice on one page. */
function Connect({
  agent,
  admin,
  row,
  held,
  onConnected,
}: {
  agent: Agent;
  admin: boolean;
  row: Connector;
  held: boolean;
  onConnected: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState<string | null>(null);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  useConnected(row.name, !held, onConnected);

  async function connect() {
    if (busy) return;
    setBusy(true);
    const outcome = await postIntent(agent.id, { verb: CONNECT_VERB[row.name] });
    setBusy(false);
    setLink(outcome.url ?? null);
    setNotice(outcome.url ? QUIET : outcomeNotice(outcome));
  }

  return (
    <div className="flex flex-col items-center gap-2xl">
      {held ? (
        <span className={cn(buttonVariants({ variant: "outline" }), "text-ink-soft")}>
          <BrandMark provider={row.name} className="size-(--size-glyph)" />
          {row.label + " connected"}
          <IconCheck className="size-icon text-ink" aria-hidden />
        </span>
      ) : admin ? (
        <Button variant="send" busy={busy} onClick={connect}>
          <BrandMark provider={row.name} onInk className="size-(--size-glyph)" />
          {"Connect " + row.label}
          <IconArrowRight className="size-icon" aria-hidden />
        </Button>
      ) : (
        <span className="text-label text-ink-soft">
          {"A workspace admin connects " + row.label + "."}
        </span>
      )}
      {link ? (
        <Notice>
          <a href={link} target="_blank" rel="noopener">
            {"Open the " + row.label + " install page"}
          </a>
        </Notice>
      ) : null}
      <OutcomeNotice state={notice} />
    </div>
  );
}

/** The last step: teammates named by address, one intent each, through the same admin-only verb
 *  chat adds a member with. Nothing reaches the address — the member exists once the verb applies,
 *  and they reach the workspace by signing in. Each one is stated as it lands, so the member reads
 *  what they added before the page hands them on.
 *
 *  The addresses are written together and committed once, rather than one at a time against an act
 *  beside the box: a member filling in their team types three names and presses once. The act that
 *  commits them stands in the page's head with the step's other acts and reaches the form by name,
 *  so the fields carry nothing but fields and the Enter key still submits. */
function Invite({
  admin,
  rows,
  added,
  onRows,
  onSubmit,
}: {
  admin: boolean;
  rows: string[];
  added: string[];
  onRows: (rows: string[]) => void;
  onSubmit: (event: FormEvent) => void;
}) {
  const first = useRef<HTMLInputElement>(null);
  useEffect(() => {
    first.current?.focus();
  }, []);

  if (!admin) {
    return <p className="m-0 text-center text-label text-ink-soft">A workspace admin adds members.</p>;
  }
  return (
    <div className="mx-auto flex w-full max-w-form flex-col gap-2xl">
      <form id={INVITE_FORM} onSubmit={onSubmit} className="flex flex-col gap-sm">
        {rows.map((value, index) => (
          <Input
            key={index}
            ref={index === 0 ? first : undefined}
            type="email"
            className="max-w-none"
            aria-label={"Email " + (index + 1)}
            placeholder="email@work.com"
            value={value}
            onChange={(event) =>
              onRows(rows.map((held, at) => (at === index ? event.target.value : held)))
            }
          />
        ))}
      </form>
      <div>
        <Button variant="row" onClick={() => onRows([...rows, ""])}>
          <IconPlus className="size-icon" aria-hidden />
          Add another
        </Button>
      </div>
      {added.length ? (
        <Notice>
          {(added.length === 1 ? "Added " : "Added " + added.length + " members: ") +
            added.join(", ") +
            "."}
        </Notice>
      ) : null}
    </div>
  );
}
