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
  usePanelRead,
} from "@/kernel/panel";
import { postAction } from "@/lib/api";
import { useWorkspaceId } from "@/lib/audience";
import { useMainAgent } from "@/lib/mainAgent";
import type { ActionView } from "@/lib/types";

const BILLING_PATH = "/ext/metronome/billing";
/* The card is the payment provider's answer and costs a round trip to Stripe, so it is read on its
   own once the balance is drawn rather than held in front of it. */
const CARD_PATH = "/ext/metronome/billing/card";

const BILLING_ACTION = "manage_billing";

const REFILL_DOLLARS = 100;
const REFILL_BELOW_DOLLARS = 25;
const MICRO_USD_PER_USD = 1e6;
const CREDIT_COLUMNS = ["Date", "Added", "Charged"];

type Card = { brand: string; last4: string };
type Purchase = { at: string; granted_micro_usd: number; charged_micro_usd: number };
type CardRead = { card: Card | null; card_unread?: boolean };

type BillingReport =
  | { limited: false }
  | {
      limited: true;
      balance_micro_usd: number;
      refused_below_micro_usd: number;
      autopay_micro_usd?: number | null;
      autopay_below_micro_usd?: number | null;
      purchases?: Purchase[];
    };

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

/** A balance is an exact figure, signed when the workspace is into its grace allowance, so it never
 *  takes `money`'s sub-cent shorthand. */
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

/** The intent carries whole dollars, so the control cannot offer cents it would have to discard. */
function wholeDollars(value: string): number | null {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

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

/** The route authenticates the session itself and answers admins only. A card cannot be saved in chat
 *  once the gate is closed, and a refill is refused until a card is on file. */
export function WorkspaceBilling({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const mainAgent = useMainAgent();
  const [state, setState] = useState<BillingState>({ phase: "asking" });
  const [cardRead, setCardRead] = useState<CardRead | null>(null);
  const [reloads, setReloads] = useState(0);
  const [refusal, setRefusal] = useState<NoticeState>(QUIET);
  const notice: NoticeState = refusal.text ? refusal : { text: place.notice ?? "", refused: false };
  const [link, setLink] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [amount, setAmount] = useState(String(REFILL_DOLLARS));
  const [below, setBelow] = useState(String(REFILL_BELOW_DOLLARS));
  const workspace = useWorkspaceId();
  const acts = usePanelRead<{ actions: ActionView[] }>(
    workspace ? "/actions/workspace/" + workspace : null,
  );
  const billing =
    acts.phase === "ready"
      ? acts.payload.actions.find((view) => view.name === BILLING_ACTION)
      : undefined;
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
  const drawn = state.phase === "ready" && state.report.limited;
  useEffect(() => {
    if (!drawn) return;
    let live = true;
    setCardRead(null);
    fetch(CARD_PATH, { credentials: "same-origin" })
      .then(async (res) => {
        if (!res.ok) throw new Error("the card could not be read");
        return (await res.json()) as CardRead;
      })
      .then((read) => {
        if (live) setCardRead(read);
      })
      .catch(() => {
        if (live) setCardRead({ card: null, card_unread: true });
      });
    return () => {
      live = false;
    };
  }, [drawn, reloads]);

  async function refill(amountDollars: number | null, belowDollars: number | null) {
    if (busy || !mainAgent || !billing) return;
    setBusy(true);
    const outcome = await postAction(mainAgent.id, billing.call, {
      operation: "autopay",
      autopay_dollars: amountDollars,
      autopay_below_dollars: belowDollars,
    });
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
    if (busy || !mainAgent || !billing) return;
    setBusy(true);
    const outcome = await postAction(mainAgent.id, billing.call, { operation: "portal" });
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
  const card = cardRead === null ? null : cardRead.card;
  const unread = cardRead?.card_unread === true;
  const pending = cardRead === null;
  const arrangeable =
    billing !== undefined && card !== null && figures.amount !== null && figures.below !== null;
  const cardBlocker = pending
    ? "Reading the payment method."
    : unread
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
                ) : pending ? (
                  "Reading the payment method"
                ) : unread ? (
                  "Payment method could not be read"
                ) : (
                  "No payment method"
                )
              }
              note={card || unread || pending ? null : "A refill charges the card saved here."}
              action={
                <Button busy={busy} disabled={!billing} onClick={saveCard}>
                  {card
                    ? "Update"
                    : unread || pending
                      ? "Open the billing portal"
                      : "Save a payment method"}
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
                  <Button busy={busy} disabled={!billing} onClick={() => refill(null, null)}>
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
