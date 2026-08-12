import { useEffect, useMemo, useState } from "react";
import { get, money, TerminalFrame, Turn, TurnDetail, when } from "./api";
import { Compactions } from "./Compactions";
import { Files } from "./Files";
import { Params } from "./nav";
import { Tail } from "./Tail";
import { Transcript } from "./Transcript";

type Details = Record<string, TurnDetail>;
type Loaded = { turns: Turn[]; details: Details };
type TimelineData = Loaded & { highlight: string | null };
const LIVE_STATUSES = new Set(["queued", "running", "parked"]);

async function loadConversation(conversationId: string): Promise<Loaded> {
  const turns = await get<Turn[]>(`conversations/${conversationId}/turns`);
  const fetched = await Promise.all(
    turns.map((turn) => get<TurnDetail>(`turns/${turn.id}`).catch(() => null)),
  );
  const details: Details = {};
  for (const detail of fetched) if (detail) details[detail.turn.id] = detail;
  return { turns, details };
}

async function timelineRootedAtParent(
  conversationId: string,
  loaded: Loaded,
): Promise<TimelineData> {
  const parentTurnId = loaded.turns.find((turn) => turn.parent_turn_id)?.parent_turn_id;
  if (!parentTurnId) return { ...loaded, highlight: null };
  const parentTurn = await get<TurnDetail>(`turns/${parentTurnId}`).catch(() => null);
  if (!parentTurn) return { ...loaded, highlight: null };
  const parent = await loadConversation(parentTurn.turn.conversation_id);
  return { ...parent, highlight: conversationId };
}

export function Session(props: {
  conversationId: string;
  selectedTurn: string | null;
  navigate: (next: Partial<Params>) => void;
}) {
  const [turns, setTurns] = useState<Turn[] | null>(null);
  const [details, setDetails] = useState<Details>({});
  const [timeline, setTimeline] = useState<TimelineData | null>(null);
  const [tab, setTab] = useState<"turns" | "transcript" | "compactions" | "files">("turns");

  useEffect(() => {
    let cancelled = false;
    setTurns(null);
    setDetails({});
    setTimeline(null);
    loadConversation(props.conversationId).then(async (loaded) => {
      if (cancelled) return;
      setTurns(loaded.turns);
      setDetails(loaded.details);
      const rooted = await timelineRootedAtParent(props.conversationId, loaded);
      if (cancelled) return;
      setTimeline(rooted);
    });
    return () => {
      cancelled = true;
    };
  }, [props.conversationId]);

  if (turns === null) return <div className="empty">loading…</div>;
  if (turns.length === 0) return <div className="empty">no turns in this conversation</div>;
  return (
    <>
      <section className="panel">
        <h2>timeline</h2>
        {timeline && (
          <Timeline
            turns={timeline.turns}
            details={timeline.details}
            highlight={timeline.highlight}
            navigate={props.navigate}
          />
        )}
      </section>
      <div className="tabs">
        {(["turns", "transcript", "compactions", "files"] as const).map((name) => (
          <button
            key={name}
            className={tab === name ? "active" : ""}
            onClick={() => setTab(name)}
          >
            {name}
          </button>
        ))}
      </div>
      {tab === "turns" &&
        turns.map((turn) => (
          <TurnCard
            key={turn.id}
            turn={turn}
            detail={details[turn.id] ?? null}
            selected={props.selectedTurn === turn.id}
            navigate={props.navigate}
          />
        ))}
      {tab === "transcript" && <Transcript conversationId={props.conversationId} />}
      {tab === "compactions" && <Compactions conversationId={props.conversationId} />}
      {tab === "files" && <Files conversationId={props.conversationId} />}
    </>
  );
}

function Timeline(props: {
  turns: Turn[];
  details: Details;
  highlight: string | null;
  navigate: (next: Partial<Params>) => void;
}) {
  const lanes = useMemo(() => {
    const rows: { turn: Turn; child: boolean }[] = [];
    for (const turn of props.turns) {
      rows.push({ turn, child: false });
      for (const child of props.details[turn.id]?.children ?? [])
        rows.push({ turn: child, child: true });
    }
    return rows;
  }, [props.turns, props.details]);

  const [start, end] = useMemo(() => {
    const starts = lanes.map(({ turn }) => Date.parse(turn.created_at));
    const ends = lanes.map(({ turn }) =>
      turn.updated_at ? Date.parse(turn.updated_at) : Date.now(),
    );
    return [Math.min(...starts), Math.max(...ends, Math.min(...starts) + 1)];
  }, [lanes]);

  return (
    <div className="timeline">
      {lanes.map(({ turn, child }) => {
        const from = ((Date.parse(turn.created_at) - start) / (end - start)) * 100;
        const to =
          (((turn.updated_at ? Date.parse(turn.updated_at) : Date.now()) - start) /
            (end - start)) *
          100;
        const label = child
          ? `↳ ${turn.subagent_profile ?? "subagent"}: ${turn.inbound}`
          : `#${turn.seq} ${turn.inbound}`;
        const current = props.highlight !== null && turn.conversation_id === props.highlight;
        return (
          <div className={`lane${current ? " current" : ""}`} key={turn.id}>
            <span
              className={`label${child ? " child" : ""}`}
              title={turn.inbound}
              onClick={() => props.navigate({ c: turn.conversation_id, t: turn.id })}
            >
              {label}
            </span>
            <span className="track">
              <span
                className={`bar ${turn.status}${child ? " child" : ""}`}
                style={{ left: `${from}%`, width: `${Math.max(to - from, 0.5)}%` }}
                title={`${turn.status} · ${when(turn.created_at)} → ${when(turn.updated_at)}`}
              />
            </span>
          </div>
        );
      })}
    </div>
  );
}

function TurnCard(props: {
  turn: Turn;
  detail: TurnDetail | null;
  selected: boolean;
  navigate: (next: Partial<Params>) => void;
}) {
  const { turn, detail } = props;
  const terminal = turn.terminal;
  return (
    <div className={`turn-card${props.selected ? " selected" : ""}`} id={`turn-${turn.id}`}>
      <div
        className="head"
        onClick={() => props.navigate({ t: props.selected ? null : turn.id })}
      >
        <code>#{turn.seq}</code>
        <span className={`chip ${turn.status}`}>{turn.status}</span>
        {turn.subagent_profile && <span className="chip subagent">{turn.subagent_profile}</span>}
        <span className="inbound">{turn.inbound}</span>
        <span className="meta">
          {terminal
            ? `${terminal.model} · ${terminal.tokens} tok · ${money(terminal.cost_micro_usd)}`
            : when(turn.created_at)}
        </span>
      </div>
      {props.selected && (
        <TurnBody turn={turn} detail={detail} navigate={props.navigate} terminal={terminal} />
      )}
    </div>
  );
}

function TurnBody(props: {
  turn: Turn;
  detail: TurnDetail | null;
  terminal: TerminalFrame | null;
  navigate: (next: Partial<Params>) => void;
}) {
  const { turn, detail, terminal } = props;
  return (
    <>
      <dl className="kv">
        <dt>turn id</dt>
        <dd>
          <code>{turn.id}</code>
        </dd>
        <dt>admitted</dt>
        <dd>
          {when(turn.created_at)} · {turn.admission_source}
          {turn.context?.sender ? ` · from ${turn.context.sender}` : ""}
        </dd>
        <dt>last update</dt>
        <dd>{when(turn.updated_at)}</dd>
        {turn.context?.source && (
          <>
            <dt>source</dt>
            <dd>
              <Source source={turn.context.source} />
            </dd>
          </>
        )}
        {turn.traceparent && (
          <>
            <dt>traceparent</dt>
            <dd>
              <code>{turn.traceparent}</code>
            </dd>
          </>
        )}
        {terminal?.error_class && (
          <>
            <dt>error</dt>
            <dd className="error-text">
              {terminal.error_class}: {terminal.error_message ?? ""}
            </dd>
          </>
        )}
      </dl>
      <details open>
        <summary>inbound</summary>
        <pre>{turn.inbound}</pre>
      </details>
      {terminal && (
        <details open>
          <summary>terminal frame</summary>
          <pre>{JSON.stringify(terminal, null, 2)}</pre>
        </details>
      )}
      {detail && detail.ledger.length > 0 && (
        <details>
          <summary>ledger ({detail.ledger.length})</summary>
          <table>
            <thead>
              <tr>
                <th>dimension</th>
                <th>amount</th>
                <th>cost</th>
                <th>model</th>
              </tr>
            </thead>
            <tbody>
              {detail.ledger.map((entry, index) => (
                <tr key={index}>
                  <td>{entry.dimension}</td>
                  <td>{entry.amount.toLocaleString()}</td>
                  <td>{money(entry.priced_micro_usd)}</td>
                  <td>{entry.model}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      {detail && detail.children.length > 0 && (
        <details open>
          <summary>subagents ({detail.children.length})</summary>
          {detail.children.map((child) => (
            <div key={child.id}>
              <span className="chip subagent">{child.subagent_profile ?? "subagent"}</span>{" "}
              <a
                href="#"
                onClick={(event) => {
                  event.preventDefault();
                  props.navigate({ c: child.conversation_id, t: child.id });
                }}
              >
                {child.inbound.slice(0, 120)}
              </a>{" "}
              <span className={`chip ${child.status}`}>{child.status}</span>
            </div>
          ))}
        </details>
      )}
      {LIVE_STATUSES.has(turn.status) && <Tail turnId={turn.id} />}
    </>
  );
}

function Source(props: { source: string }) {
  const [head, ...rest] = props.source.split(" ");
  if (!/^https?:\/\//.test(head)) return <>{props.source}</>;
  return (
    <>
      <a href={head} target="_blank" rel="noopener">
        {head}
      </a>
      {rest.length > 0 && ` ${rest.join(" ")}`}
    </>
  );
}
