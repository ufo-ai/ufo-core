import { type ReactNode, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Table, Td, Th, tableFloor } from "@/components/ui/table";
import type { Placement } from "@/kernel/pager";
import {
  Notice,
  type NoticeState,
  OutcomeNotice,
  PanelBlank,
  QUIET,
  Section,
  outcomeNotice,
} from "@/kernel/panel";
import { postObjectAction } from "@/lib/api";
import { useWorkspaceId } from "@/lib/audience";
import { useMainAgent } from "@/lib/mainAgent";

const BILLING_PATH = "/ext/metronome/billing";

const BILLING_ACTION = "manage_billing";

const REFILL_DOLLARS = 100;
const REFILL_BELOW_DOLLARS = 25;
const MICRO_USD_PER_USD = 1e6;
const CREDIT_COLUMNS = ["Date", "Added", "Charged"];

type Card = { brand: string; last4: string };
type Purchase = { at: string; granted_micro_usd: number; charged_micro_usd: number };

type BillingReport =
  | { limited: false }
  | {
      limited: true;
      balance_micro_usd: number;
      refused_below_micro_usd: number;
      card: Card | null;
      card_unread?: boolean;
      autopay_micro_usd?: number | null;
      autopay_below_micro_usd?: number | null;
      purchases?: Purchase[];
    };

/** Why the read did not answer. The tab is drawn on every deploy, and only the status says which of
 *  three unrelated things happened: no billing extension is installed so the route is not mounted at
 *  all, the reader is not an admin, or the read failed. One sentence for all three states a refusal
 *  on a deploy that has no billing to refuse. */
type Unanswered = "absent" | "forbidden" | "unreadable";

type BillingState =
  | { phase: "asking" }
  | { phase: "silent"; why: Unanswered }
  | { phase: "ready"; report: BillingReport };

const UNANSWERED: Record<Unanswered, string> = {
  absent: "This deploy does not carry billing.",
  forbidden: "Only a workspace admin can read billing.",
  unreadable: "Billing could not be read.",
};

function unanswered(status: number): Unanswered {
  if (status === 404) return "absent";
  if (status === 401 || status === 403) return "forbidden";
  return "unreadable";
}

/** A balance is an exact figure, signed when the workspace is into its grace allowance, so it
 *  never takes `money`'s sub-cent shorthand. */
function dollars(micro: number): string {
  return (micro < 0 ? "-" : "") + "$" + (Math.abs(micro) / MICRO_USD_PER_USD).toFixed(2);
}

function refillRule(amountMicro: number, belowMicro: number): string {
  return (
    "Adding " + dollars(amountMicro) + " when the balance falls below " + dollars(belowMicro) + "."
  );
}

function creditDate(value: string): string {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(value));
}

/** A whole-dollar field: the intent carries whole dollars, so the control cannot offer cents it
 *  would have to discard. An unreadable or non-positive entry answers null and the save refuses
 *  rather than sending a figure the member did not mean. */
function wholeDollars(value: string): number | null {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

/** One fact and the act that changes it, side by side — the shape a billing screen is read in: what
 *  is true now on the left, the one thing to do about it on the right. */
function Standing({
  statement,
  note,
  action,
}: {
  statement: ReactNode;
  note?: ReactNode;
  action: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-lg">
      <div className="flex flex-col gap-2xs">
        <span className="text-ink">{statement}</span>
        {note ? <span className="text-small text-ink-soft">{note}</span> : null}
      </div>
      {action}
    </div>
  );
}

function Amount({
  label,
  value,
  onValue,
  disabled,
}: {
  label: string;
  value: string;
  onValue: (value: string) => void;
  disabled: boolean;
}) {
  return (
    <label className="flex items-center gap-sm text-label">
      <span className="text-ink-soft">{label}</span>
      <span className="flex items-center gap-2xs">
        <span aria-hidden>$</span>
        <input
          type="number"
          min="1"
          step="1"
          inputMode="numeric"
          value={value}
          disabled={disabled}
          onChange={(event) => onValue(event.target.value)}
          aria-label={label}
          className="w-20 rounded-control border border-edge bg-surface px-sm py-2xs text-ink disabled:text-ink-soft"
        />
      </span>
    </label>
  );
}

function Credits({ rows }: { rows: Purchase[] }) {
  if (!rows.length) return <PanelBlank body="Nothing has been added to this balance yet." />;
  return (
    <Table columns={CREDIT_COLUMNS} floor={tableFloor({ prose: 1, fact: 2 })}>
      <thead>
        <tr>
          {CREDIT_COLUMNS.map((column) => (
            <Th key={column}>{column}</Th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.at}>
            <Td className="w-full whitespace-nowrap">{creditDate(row.at)}</Td>
            <Td className="whitespace-nowrap">{dollars(row.granted_micro_usd)}</Td>
            <Td className="whitespace-nowrap">{dollars(row.charged_micro_usd)}</Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

/** The workspace's prepaid balance, the card behind it, and the rule that keeps it funded — read
 *  from the metronome extension's own endpoint rather than the panel API. The route authenticates
 *  the session itself and answers admins only.
 *
 *  Both acts live here because the workspace that most needs them is the one whose balance refuses
 *  every turn: a card cannot be saved in chat once the gate is closed, and a refill is refused
 *  until a card is on file. The refill control is therefore drawn even with no card, disabled and
 *  stating what unblocks it, so the member reads the whole path at once. */
export function WorkspaceBilling({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const mainAgent = useMainAgent();
  const [state, setState] = useState<BillingState>({ phase: "asking" });
  const [reloads, setReloads] = useState(0);
  // What an act left, split the way every placed screen splits it: an act that applied is the
  // pane's own outcome and rides the place, and a refusal belongs to this mount alone.
  const [refusal, setRefusal] = useState<NoticeState>(QUIET);
  const notice: NoticeState = refusal.text ? refusal : { text: place.notice ?? "", refused: false };
  const [link, setLink] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [amount, setAmount] = useState(String(REFILL_DOLLARS));
  const [below, setBelow] = useState(String(REFILL_BELOW_DOLLARS));
  const workspace = useWorkspaceId();
  useEffect(() => {
    let live = true;
    fetch(BILLING_PATH, { credentials: "same-origin" })
      .then(async (res) => {
        if (!res.ok) {
          if (live) setState({ phase: "silent", why: unanswered(res.status) });
          return;
        }
        const report = (await res.json()) as BillingReport;
        if (live) setState({ phase: "ready", report });
      })
      .catch(() => {
        if (live) setState({ phase: "silent", why: "unreadable" });
      });
    return () => {
      live = false;
    };
  }, [reloads]);

  async function refill(amountDollars: number | null, belowDollars: number | null) {
    if (busy || !mainAgent || !workspace) return;
    setBusy(true);
    const outcome = await postObjectAction(
      mainAgent.id,
      { kind: "workspace", name: workspace, action: BILLING_ACTION },
      {
        operation: "autopay",
        autopay_dollars: amountDollars,
        autopay_below_dollars: belowDollars,
      },
    );
    setBusy(false);
    if (outcome.applied) {
      setRefusal(QUIET);
      onPlace({ notice: outcome.message });
      setReloads((count) => count + 1);
      return;
    }
    setRefusal(outcomeNotice(outcome));
  }

  async function saveCard() {
    if (busy || !mainAgent || !workspace) return;
    setBusy(true);
    const outcome = await postObjectAction(
      mainAgent.id,
      { kind: "workspace", name: workspace, action: BILLING_ACTION },
      { operation: "portal" },
    );
    setBusy(false);
    setLink(outcome.url ?? null);
    setRefusal(outcome.url ? QUIET : outcomeNotice(outcome));
  }

  if (state.phase === "asking") return null;
  if (state.phase === "silent") return <PanelBlank body={UNANSWERED[state.why]} />;
  const report = state.report;
  if (!report.limited) return <PanelBlank body="This workspace has no spending limit." />;
  const rule =
    typeof report.autopay_micro_usd === "number" &&
    typeof report.autopay_below_micro_usd === "number"
      ? refillRule(report.autopay_micro_usd, report.autopay_below_micro_usd)
      : null;
  const figures = { amount: wholeDollars(amount), below: wholeDollars(below) };
  const card = report.card;
  // A provider that would not answer leaves the card unknown, which is not the same as absent: it
  // must not read as an invitation to save one, and it cannot license arranging a refill either.
  const unread = report.card_unread === true;
  const arrangeable = card !== null && figures.amount !== null && figures.below !== null;
  const cardBlocker = unread
    ? "The card provider did not answer. The balance above is current."
    : "Save a payment method to arrange refills.";
  const stopped = report.balance_micro_usd <= report.refused_below_micro_usd;

  return (
    <>
      <Section>
        <div className="rounded-panel border border-edge bg-surface p-xl">
          <div className="text-title font-strong">{dollars(report.balance_micro_usd)}</div>
          <div className="mt-2xs text-small text-ink-soft">Current balance</div>
          {stopped ? (
            <div className="mt-lg text-small text-ink">
              {"Turns are refused until it is back above " +
                dollars(report.refused_below_micro_usd) +
                "."}
            </div>
          ) : null}
        </div>
        <OutcomeNotice state={notice} />
      </Section>
      {mainAgent ? (
        <>
          <Section title="Payment">
            <Standing
              statement={
                card ? (
                  <span className="capitalize">{card.brand + " •••• " + card.last4}</span>
                ) : unread ? (
                  "Payment method could not be read"
                ) : (
                  "No payment method"
                )
              }
              note={card || unread ? null : "A refill charges the card saved here."}
              action={
                <Button busy={busy} onClick={saveCard}>
                  {card ? "Update" : unread ? "Open the billing portal" : "Save a payment method"}
                </Button>
              }
            />
            {link ? (
              <Notice>
                <a href={link} target="_blank" rel="noopener">
                  Open the billing portal
                </a>
              </Notice>
            ) : null}
          </Section>
          <Section title="Automatic refills">
            {rule ? (
              <Standing
                statement={rule}
                action={
                  <Button busy={busy} onClick={() => refill(null, null)}>
                    Stop
                  </Button>
                }
              />
            ) : (
              <Standing
                statement={
                  <span className="flex flex-wrap items-center gap-xl">
                    <Amount label="Add" value={amount} onValue={setAmount} disabled={!card} />
                    <Amount
                      label="When the balance falls below"
                      value={below}
                      onValue={setBelow}
                      disabled={!card}
                    />
                  </span>
                }
                note={card ? null : cardBlocker}
                action={
                  <Button
                    busy={busy}
                    disabled={!arrangeable}
                    onClick={() => refill(figures.amount, figures.below)}
                  >
                    Turn on
                  </Button>
                }
              />
            )}
          </Section>
          <Section title="Credit added">
            <Credits rows={report.purchases ?? []} />
          </Section>
        </>
      ) : null}
    </>
  );
}
