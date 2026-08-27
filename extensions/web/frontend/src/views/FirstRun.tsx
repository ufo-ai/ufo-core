import {
  IconCheck,
  IconCircleCheck,
  IconMessages,
  IconPlugConnected,
  IconPlus,
} from "@tabler/icons-react";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { ToggleGroup, ToggleGroupItem, ToggleGroupOne } from "@/components/ui/toggle-group";
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
import mark from "@brand/ufo-mark.svg";
import { postIntent } from "@/lib/api";
import { BrandMark } from "@/lib/brandMark";
import { cn } from "@/lib/cn";
import { ConsentLink, openConsentWindow } from "@/lib/consent";
import { setPendingAsk } from "@/lib/pendingAsk";
import type { Agent, Member } from "@/lib/types";

type ProviderTile = { name: string; label: string; summary: string; group: string };

type Connector = ProviderTile & { installed: boolean };

export type FirstRunPayload = {
  providers: ProviderTile[];
  connectors: Connector[];
  imessage: boolean;
};

const GOAL_STEP = "goal";

const GOALS = [
  {
    name: "product-development",
    label: "Faster product dev",
    prompt: "develop products faster",
  },
  { name: "revenue", label: "More revenue", prompt: "increase revenue" },
  { name: "operations", label: "Automate ops", prompt: "automate operations" },
  { name: "product-market-fit", label: "Find PMF", prompt: "find product-market fit" },
] as const;

type Goal = (typeof GOALS)[number]["name"];

function opening(goal: Goal | "", detail: string, labels: string[]): string {
  const selected = GOALS.find((option) => option.name === goal);
  const context = detail.trim().replace(/[.!?]+$/, "");
  const setup = "I just set up this workspace. ";
  if (!selected) {
    const tools = labels.length ? " We use " + labels.join(", ") + "." : "";
    return setup + "I want an agent to help with this goal: " + context + "." + tools;
  }
  const tools = labels.length ? ", and we use " + labels.join(", ") : "";
  const more = context ? " More context: " + context + "." : "";
  return setup + "I want to " + selected.prompt + tools + "." + more;
}

/** The connector catalog and the workspace's two installs — the read behind both selectors. The
 *  first run reads it for the steps it draws, a connect step waiting on an install reads it again
 *  for the single fact it is waiting on, and the Connect page reads it for the tiles it offers. */
export const FIRST_RUN_READ = "/workspace/first-run";

/** How often a waiting selector re-reads. The install happens on the provider's own pages, so
 *  nothing here can say when it lands — the wait is the member's, and it is measured in the
 *  seconds they spend over there rather than in the half-minute a pane showing records can hold a
 *  stale answer for. */
export const WATCH_MS = 3_000;

/** One member's claim on the phone the iMessage step reserved: `pending` while the reservation
 *  still waits for the code, `connected` once the phone proved itself, and `expired` once the
 *  window lapsed without a proof. `expired` is carried by the read rather than derived from a
 *  timestamp, so the page and the surface agree on which side of the window a poll landed on. */
type ImessageClaim = { state: "pending" | "connected" | "expired" };

/** The claim one member holds on the surface_address rows the iMessage step writes. The address is
 *  the member's own stated phone, so the read answers only about the reader's claim and holds no
 *  other member's. */
const IMESSAGE_CLAIM_READ = "/workspace/imessage-claim";

/** How often the pending iMessage step re-reads the claim, on the same rhythm a waiting connector
 *  selector holds. The proof happens on the phone, so the wait is the member's and the page cannot
 *  be told when it lands — it asks. */
export const IMESSAGE_WATCH_MS = 3_000;

/** The intent a workspace install dispatches, keyed by the connector that takes one. The named
 *  tool mints the install link inside the turn and the outcome carries it back, so installing
 *  takes no message the member has to send. Every provider outside this map connects a member's
 *  own account through the broker verb instead. */
export const CONNECT_VERB: Record<string, string> = {
  slack: "connect_slack",
  github: "connect_github",
};

/** The name a member types to reach the agent in that product, set apart from the sentence around
 *  it so the handle itself is what the eye lands on. */
function Mention({ children }: { children: ReactNode }) {
  return <strong className="font-strong">{children}</strong>;
}

/** Each connector gets the whole step, and the step names the agent the member is about to let in
 *  rather than calling it `the app`: what they are deciding is whether they want this agent in that
 *  product, so the step says the handle they will type once it is there. */
const CONNECT_COPY: Record<
  string,
  { title: ReactNode; note: ReactNode; icon: typeof IconMessages }
> = {
  slack: {
    title: (
      <>
        Add <Mention>@ufo</Mention> to Slack
      </>
    ),
    note: (
      <>
        Mention <Mention>@ufo</Mention> in a channel or send a direct message.{" "}
        <Mention>@ufo</Mention> replies, remembers, and works with your team.
      </>
    ),
    icon: IconMessages,
  },
  github: {
    title: (
      <>
        Add the <Mention>ufo-ai</Mention> bot to GitHub
      </>
    ),
    note: (
      <>
        <Mention>ufo-ai</Mention> reads code, reviews pull requests, and pushes changes. You pick
        which repositories on GitHub.
      </>
    ),
    icon: IconPlugConnected,
  },
};

const TOOLS_STEP = "tools";
const TEAM_STEP = "team";
const IMESSAGE_STEP = "imessage";
const US_PHONE_PATTERN = /^[2-9][0-9]{2}[2-9][0-9]{6}$/;

/** The invite step's form, named so the act that submits it can stand in the page's head with the
 *  other acts rather than inside the fields it commits. */
const INVITE_FORM = "first-run-invite";

/** Blank addresses the invite step opens with. A team is more than one person, so the step asks as
 *  though several are coming: one box states that a teammate is an afterthought, and a member with
 *  more to add says so with `Add another`. */
const INVITE_ROWS = 3;

/** What each step asks, keyed by the step's own name — which for a connector is the connector's
 *  slug, so a step and the copy over it cannot drift apart. */
const STEP_COPY: Record<string, { title: ReactNode; note: ReactNode }> = {
  [GOAL_STEP]: {
    title: "What do you want an agent to do for you today?",
    note: "Pick one goal or describe another.",
  },
  [TOOLS_STEP]: {
    title: "What your team uses",
    note: (
      <>
        Pick the ones your team works in, so <Mention>@ufo</Mention> knows where your work lives.
      </>
    ),
  },
  [TEAM_STEP]: {
    title: "Invite your team",
    note: "ufo.ai is best with a team, and we don't charge per seat.",
  },
  [IMESSAGE_STEP]: {
    title: "Use this agent in iMessage",
    note: "Connect your phone to message UFO anytime.",
  },
  ...CONNECT_COPY,
};

/** The page the first run is read on, with the workspace mark, progress, and the current step's
 *  acts in its head. It has no navigation because an unset workspace has nowhere else to go, and
 *  offering destinations would pull the member away from finishing setup. The rest of the
 *  viewport centres the step's question and answer together, under whatever mark leads it. */
function Frame({
  title,
  note,
  lead,
  actions,
  at,
  steps,
  children,
}: {
  title?: ReactNode;
  note?: ReactNode;
  lead?: ReactNode;
  actions?: ReactNode;
  at?: number;
  steps?: number;
  children: ReactNode;
}) {
  return (
    <main className="grid h-dvh grid-rows-[auto_1fr] overflow-y-auto">
      <div className="grid h-8xl grid-cols-3 items-center px-7xl max-narrow:px-2xl">
        <span
          role="img"
          aria-label="ufo"
          className="block size-(--size-glyph) shrink-0 bg-current"
          style={{ mask: `url(${mark}) center / contain no-repeat` }}
        />
        {at !== undefined && steps !== undefined ? (
          <div
            role="progressbar"
            aria-label="Step"
            aria-valuemin={1}
            aria-valuemax={steps}
            aria-valuenow={at + 1}
            className="flex items-center gap-2xs justify-self-center"
          >
            {Array.from({ length: steps }, (_, index) => (
              <span
                key={index}
                aria-hidden
                className={cn(
                  "h-2xs rounded-full",
                  index === at ? "w-(--size-step-on) bg-ink" : "w-(--size-step-off) bg-edge",
                )}
              />
            ))}
          </div>
        ) : null}
        <div className="flex items-center justify-self-end gap-sm">{actions}</div>
      </div>
      <div className="mx-auto flex min-h-0 w-full max-w-section flex-col items-center justify-center gap-6xl px-2xl py-6xl">
        {lead}
        {title ? (
          <div className="flex max-w-form flex-col gap-2xl text-center">
            <h1 className="m-0 text-subtitle font-medium text-ink">{title}</h1>
            {note ? <p className="m-0 text-label text-ink-soft">{note}</p> : null}
          </div>
        ) : null}
        {children}
      </div>
    </main>
  );
}

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
  const [goal, setGoal] = useState<Goal | "">("");
  const [detail, setDetail] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [recorded, setRecorded] = useState<string[] | null>(null);
  const [connected, setConnected] = useState<string[]>([]);
  const [rows, setRows] = useState<string[]>(() => Array<string>(INVITE_ROWS).fill(""));
  const [added, setAdded] = useState<string[]>([]);
  const [imessageOffer, setImessageOffer] = useState(false);
  const [phone, setPhone] = useState("");
  const [imessageLink, setImessageLink] = useState<string | null>(null);
  const [imessageMessage, setImessageMessage] = useState("");
  const [imessageReady, setImessageReady] = useState(false);
  const imessageClaim = usePanelRead<ImessageClaim>(
    imessageReady ? IMESSAGE_CLAIM_READ : null,
    0,
    IMESSAGE_WATCH_MS,
  );
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
    setAt(at + 1);
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
        /** The run the member is on. Three steps are certain from the start — the goal, the tools,
         *  and the team — so the head states three until the tools are recorded, and a connector
         *  picked there lengthens the run by its own step. Holding the team back until then would
         *  have the head announce the run finished while a step it always draws is still to come. */
        const revealed =
          recorded === null
            ? [GOAL_STEP, TOOLS_STEP, TEAM_STEP]
            : [GOAL_STEP, TOOLS_STEP, ...chosen.map((row) => row.name), TEAM_STEP];
        const step = imessageOffer ? IMESSAGE_STEP : revealed[at];
        const connector = chosen.filter((row) => row.name === step)[0];
        const ConnectIcon =
          step === IMESSAGE_STEP
            ? IconMessages
            : connector
              ? CONNECT_COPY[connector.name].icon
              : null;
        const held = connector ? connector.installed || connected.includes(step) : false;
        const finish = () => {
          setPendingAsk(
            agent.id,
            opening(
              goal,
              detail,
              payload.providers
                .filter((tile) => (recorded ?? []).includes(tile.name))
                .map((tile) => tile.label),
            ),
            true,
          );
          onOpenChat();
        };
        const advance = () => (at + 1 < revealed.length ? setAt(at + 1) : finish());
        const offerImessage = () => (payload.imessage ? setImessageOffer(true) : finish());
        const back = () => (imessageOffer ? setImessageOffer(false) : setAt(at - 1));
        const skip = () => (step === TEAM_STEP ? offerImessage() : advance());
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
          offerImessage();
        };
        const connectImessage = async (event: FormEvent) => {
          event.preventDefault();
          if (busy) return;
          const stated = phone.trim();
          const digits = stated.replace(/\D/g, "");
          const foreign = stated.includes("+") && !digits.startsWith("1");
          if (foreign || !US_PHONE_PATTERN.test(digits)) {
            setNotice({
              text: "Enter a 10-digit US phone number.",
              refused: true,
            });
            return;
          }
          setBusy(true);
          const outcome = await postIntent(agent.id, {
            verb: "connect_imessage",
            phone_number: `+1${digits}`,
          });
          setBusy(false);
          if (!outcome.applied) {
            setNotice(outcomeNotice(outcome));
            return;
          }
          setNotice(QUIET);
          setImessageLink(outcome.url ?? null);
          setImessageMessage(outcome.message);
          setImessageReady(true);
        };
        /** What the lapsed notice points the member at. Clearing the readiness draws the phone form
         *  again, holding the number they already wrote, so one press mints another code where the
         *  first one was minted. */
        const connectAgain = () => setImessageReady(false);
        const imessageLinkEnd = imessageMessage.indexOf(" from ");
        const imessageClaimed =
          imessageClaim.phase === "ready" && imessageClaim.payload.state === "connected";
        const imessageLapsed =
          imessageClaim.phase === "ready" && imessageClaim.payload.state === "expired";
        return (
          <Frame
            title={STEP_COPY[step].title}
            note={STEP_COPY[step].note}
            lead={
              ConnectIcon ? (
                <span className="flex size-(--size-badge) items-center justify-center rounded-full bg-ink">
                  <ConnectIcon
                    className="size-(--size-badge-glyph) text-surface"
                    stroke={1.5}
                    aria-hidden
                  />
                </span>
              ) : undefined
            }
            at={at}
            steps={revealed.length}
            actions={
              <>
                {at > 0 ? (
                  <Button size="bar" className="h-10" onClick={back}>
                    Back
                  </Button>
                ) : null}
                {step === GOAL_STEP || step === TOOLS_STEP ? null : (
                  <Button size="bar" className="h-10" onClick={skip}>
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
                ) : step === IMESSAGE_STEP ? null : (
                  <Button
                    variant="send"
                    size="bar"
                    className="h-10 w-32"
                    busy={busy}
                    disabled={
                      (step === GOAL_STEP && !goal && !detail.trim()) ||
                      (connector !== undefined && !held)
                    }
                    onClick={step === TOOLS_STEP ? record : advance}
                  >
                    Continue
                  </Button>
                )}
              </>
            }
          >
            <OutcomeNotice state={notice} />
            {step === GOAL_STEP ? (
              <div className="flex w-(--container-answer) max-w-full flex-col gap-6xl">
                <ToggleGroupOne
                  className="flex flex-col gap-2xs"
                  value={goal}
                  onValueChange={(value) => setGoal(value as Goal | "")}
                >
                  {GOALS.map((option) => (
                    <ToggleGroupItem
                      key={option.name}
                      value={option.name}
                      className={cn(
                        "group flex h-10 w-full items-center justify-between rounded-(--radius-answer)",
                        "border-0 bg-fill px-2xl text-start text-label text-ink hover:bg-fill-strong",
                        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink",
                      )}
                    >
                      {option.label}
                      <IconCheck
                        className={cn(
                          "size-(--size-glyph) shrink-0 text-ink-soft opacity-0",
                          "group-data-[state=on]:opacity-100",
                        )}
                        aria-hidden
                      />
                    </ToggleGroupItem>
                  ))}
                </ToggleGroupOne>
                <Input
                  surface="answer"
                  aria-label="Add context"
                  placeholder="More information"
                  value={detail}
                  onChange={(event) => setDetail(event.target.value)}
                />
              </div>
            ) : null}
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
            {step === IMESSAGE_STEP ? (
              <div className="flex w-full max-w-(--container-connect) flex-col gap-2xl px-2xl">
                {imessageReady ? (
                  imessageClaimed ? (
                    <>
                      <div className="flex items-center justify-center gap-sm text-ink">
                        <IconCircleCheck className="size-(--size-glyph)" aria-hidden />
                        <p className="m-0 text-label">Phone connected — you can now message UFO from iMessage.</p>
                      </div>
                      <Button variant="send" size="bar" className="h-10 w-full" onClick={finish}>
                        Start chatting
                      </Button>
                    </>
                  ) : (
                    <>
                      <Notice>
                        {imessageLapsed ? (
                            "That code expired. Connect iMessage again for a new one."
                          ) : imessageLink ? (
                            <a
                              href={imessageLink}
                              className="text-inherit underline underline-offset-2 hover:text-ink"
                            >
                              {imessageLinkEnd < 0
                                ? imessageMessage
                                : imessageMessage.slice(0, imessageLinkEnd)}
                            </a>
                          ) : (
                            imessageMessage
                          )}
                          {imessageLink && !imessageLapsed && imessageLinkEnd >= 0
                            ? imessageMessage.slice(imessageLinkEnd)
                            : null}
                      </Notice>
                      {imessageLapsed ? (
                        <Button
                          variant="send"
                          size="bar"
                          className="h-10 w-full"
                          onClick={connectAgain}
                        >
                          Connect iMessage again
                        </Button>
                      ) : imessageLink ? (
                        <a
                          href={imessageLink}
                          className={cn(
                            buttonVariants({ variant: "send", size: "bar" }),
                            "h-10 w-full",
                          )}
                        >
                          Text code to UFO
                        </a>
                      ) : null}
                      <Button variant="outline" size="bar" className="h-10 w-full" onClick={finish}>
                        Nevermind
                      </Button>
                    </>
                  )
                ) : (
                  <form
                    onSubmit={connectImessage}
                    className="flex flex-col gap-2xl"
                  >
                    <Input
                      surface="answer"
                      type="tel"
                      aria-label="iMessage phone number"
                      autoComplete="tel-national"
                      inputMode="numeric"
                      placeholder="(555) 555-5555"
                      value={phone}
                      onChange={(event) => {
                        const printed = event.target.value;
                        const stated = printed.replace(/\D/g, "");
                        if (
                          (printed.includes("+") && !stated.startsWith("1")) ||
                          (stated.length > 10 && !(stated.length === 11 && stated.startsWith("1")))
                        ) {
                          setPhone(printed);
                          return;
                        }
                        const digits = (
                          stated.length === 11 && stated.startsWith("1") ? stated.slice(1) : stated
                        ).slice(0, 10);
                        if (digits.length < 4) setPhone(digits);
                        else if (digits.length < 7)
                          setPhone(`(${digits.slice(0, 3)}) ${digits.slice(3)}`);
                        else
                          setPhone(
                            `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`,
                          );
                      }}
                    />
                    <Button
                      type="submit"
                      variant="send"
                      size="bar"
                      className="h-10 w-full"
                      busy={busy}
                      disabled={!phone.trim()}
                    >
                      Connect iMessage
                    </Button>
                  </form>
                )}
              </div>
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

/** One connector's own step. The act dispatches that connector's admin-gated tool, which seals the
 *  install link for this workspace and answers with it — a non-admin is told who installs it rather
 *  than pressing an act the workspace refuses. The link expires, so the act stays on the step and
 *  mints another.
 *
 *  Pressing it is the whole thing: the consent window opens on the press and the minted link lands
 *  in it, rather than appearing under the button as a second thing to find. A browser that refuses
 *  the window is the only case that still renders the link, because then there is nothing else to
 *  carry the member over.
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
    // Opened on the press, before the round trip that mints the link: a window opened afterwards
    // has lost the gesture the browser opens one for. It waits on the provider's own page.
    const consent = openConsentWindow();
    const outcome = await postIntent(agent.id, { verb: CONNECT_VERB[row.name] });
    setBusy(false);
    if (consent && outcome.url) consent.location.href = outcome.url;
    if (consent && !outcome.url) consent.close();
    // The step keeps the link only for a member whose browser refused the window, so pressing once
    // is the whole act for everybody else.
    setLink(consent ? null : (outcome.url ?? null));
    setNotice(outcome.url ? QUIET : outcomeNotice(outcome));
  }

  return (
    <div className="flex w-full max-w-(--container-connect) flex-col items-center gap-sm px-2xl">
      {held ? (
        <span
          className={cn(
            buttonVariants({ variant: "outline", size: "bar" }),
            "h-10 w-full text-ink-soft",
          )}
        >
          <BrandMark provider={row.name} className="size-(--size-glyph)" />
          {row.label + " connected"}
          <IconCheck className="size-icon text-ink" aria-hidden />
        </span>
      ) : admin ? (
        <Button variant="send" size="bar" className="h-10 w-full" busy={busy} onClick={connect}>
          <BrandMark provider={row.name} onInk className="size-(--size-glyph)" />
          {"Connect " + row.label}
        </Button>
      ) : (
        <span className="flex h-10 w-full items-center justify-center text-center text-label text-ink-soft">
          {"A workspace admin connects " + row.label + "."}
        </span>
      )}
      {link ? (
        <Notice>
          <ConsentLink url={link}>{"Open the " + row.label + " install page"}</ConsentLink>
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
    <div className="flex w-(--container-answer) max-w-full flex-col gap-2xl">
      <form id={INVITE_FORM} onSubmit={onSubmit} className="flex flex-col gap-2xs">
        {rows.map((value, index) => (
          <Input
            key={index}
            surface="answer"
            ref={index === 0 ? first : undefined}
            type="email"
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
        <Button variant="outline" size="bar" onClick={() => onRows([...rows, ""])}>
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
