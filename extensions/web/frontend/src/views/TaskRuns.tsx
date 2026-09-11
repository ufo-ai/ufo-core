import { useEffect, useState } from "react";
import { IconSettings } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { Sheet } from "@/components/ui/sheet";
import { ObjectDetail, objectAt, slotOf } from "@/kernel/objects";
import type { ObjectAddress, ObjectRow } from "@/kernel/objects";
import type { Placement } from "@/kernel/pager";
import { Pager } from "@/kernel/pager";
import { FacetMenu, PageToolbar } from "@/kernel/pane";
import type { FacetGroup } from "@/kernel/pane";
import { Loading, Panel, PanelBlank, PanelEmpty, Section, usePanelRead } from "@/kernel/panel";
import { RowLines } from "@/kernel/rows";
import { closed, opened } from "@/kernel/slots";
import { useMe } from "@/lib/audience";
import { cn } from "@/lib/cn";
import { useAgents } from "@/lib/mainAgent";
import { Moment } from "@/lib/moments";
import { ChatPane } from "@/views/ChatPane";

const TURN_KIND = "turn";
const TASK_KIND = "scheduled_task";
const TRIGGER_KIND = "source_trigger";
const RUN_PREFIX = "run/";
const RUN = "Run";
const SETTINGS = "Settings";
const NO_RUNS = "No run yet.";
const NO_RUNS_FOR_ONE = "No run yet for this one.";
const NOT_ON_PAGE = "That run is not on this page.";
const NO_AGENT = "The app that ran this is not listed for you.";
const SCOPE_SEPARATOR = "/";
const LIVE = new Set(["queued", "running", "parked"]);
const STATUS_WORDS: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  parked: "Waiting",
  done: "Done",
  failed: "Failed",
  cancelled: "Stopped",
};
/** A run moves in seconds while it works, so a page holding one re-reads at that rate; a page of
 *  settled runs says the same thing every time it is asked. */
const WORKING_MS = 4_000;
const RESTING_MS = 30_000;

type Run = {
  name: string;
  agent: string;
  title: string;
  status: string;
  source: string;
  sourceName: string;
  conversation: string;
  createdAt: string;
  origin: string;
  text: string;
};

type IndexRow = ObjectRow & { agent_id: string };
type IndexPayload = { objects: IndexRow[]; next_cursor: string | null };
type DetailPayload = { name: string; status: Record<string, string | number | boolean | null> };

function said(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function toRun(row: IndexRow): Run {
  return {
    name: row.name,
    agent: row.agent_id,
    title: said(row.title) || said(row.source_name),
    status: said(row.status),
    source: said(row.source),
    sourceName: said(row.source_name),
    conversation: said(row.conversation),
    createdAt: said(row.created_at),
    origin: said(row.origin),
    text: said(row.text),
  };
}

/** A scope is one task or trigger, spelled `<kind>/<name>` so one filter key narrows to either. */
function scoped(scope: string): { kind: string; name: string } | null {
  const at = scope.indexOf(SCOPE_SEPARATOR);
  if (at <= 0 || at === scope.length - 1) return null;
  return { kind: scope.slice(0, at), name: scope.slice(at + 1) };
}

function runsPath(scope: string, after: string | undefined): string {
  const params = new URLSearchParams({ fired: "true", order_by: "created_at", order: "desc" });
  const one = scoped(scope);
  if (one) {
    params.set("source", one.kind);
    params.set("source_name", one.name);
  }
  if (after) params.set("cursor", after);
  return "/objects/" + TURN_KIND + "?" + params.toString();
}

function RunMark({ status }: { status: string }) {
  const live = LIVE.has(status);
  return (
    <span
      role="img"
      aria-label={STATUS_WORDS[status] ?? status}
      className={cn(
        "mt-xs size-sm shrink-0 rounded-full",
        live
          ? "bg-live animate-pulse motion-reduce:animate-none"
          : status === "failed"
            ? "bg-blocked"
            : status === "cancelled"
              ? "bg-ink-faint"
              : "bg-edge",
      )}
    />
  );
}

function firstLine(text: string): string {
  return text.split("\n").find((line) => line.trim()) ?? "";
}

function Filter({ scope, onPick }: { scope: string; onPick: (scope: string) => void }) {
  const tasks = usePanelRead<IndexPayload>("/objects/" + TASK_KIND);
  const triggers = usePanelRead<IndexPayload>("/objects/" + TRIGGER_KIND);
  const groups: FacetGroup[] = [];
  if (tasks.phase === "ready" && tasks.payload.objects.length)
    groups.push({
      label: "Tasks",
      options: tasks.payload.objects.map((row) => ({
        label: row.name,
        value: TASK_KIND + SCOPE_SEPARATOR + row.name,
      })),
    });
  if (triggers.phase === "ready" && triggers.payload.objects.length)
    groups.push({
      label: "Triggers",
      options: triggers.payload.objects.map((row) => ({
        label: row.summary,
        value: TRIGGER_KIND + SCOPE_SEPARATOR + row.name,
      })),
    });
  return <FacetMenu groups={groups} value={scope} onPick={onPick} />;
}

/** The list, reporting the rows it drew upward so a drawer opened on one of them knows the run's
 *  agent and conversation without a read of its own. */
function Runs({
  place,
  onPlace,
  onShown,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
  onShown: (runs: Run[]) => void;
}) {
  const scope = place.scope ?? "";
  const opens = place.opens ?? [];
  const [working, setWorking] = useState(false);
  const state = usePanelRead<IndexPayload>(
    runsPath(scope, place.after),
    0,
    working ? WORKING_MS : RESTING_MS,
  );
  const payload = state.phase === "ready" ? state.payload : null;
  useEffect(() => {
    if (payload === null) return;
    const runs = payload.objects.map(toRun);
    setWorking(runs.some((run) => LIVE.has(run.status)));
    onShown(runs);
  }, [onShown, payload]);
  const settings = (run: Run): ObjectAddress => ({
    agent: run.agent,
    kind: run.source,
    name: run.sourceName,
  });
  return (
    <Panel state={state}>
      {(held) => {
        const rows = held.objects.map(toRun);
        if (!rows.length) return <PanelBlank body={scope ? NO_RUNS_FOR_ONE : NO_RUNS} />;
        return (
          <>
            <RowLines
              rows={rows}
              rowKey={(run) => run.name}
              mark={(run) => <RunMark status={run.status} />}
              primary={(run) => run.title || RUN}
              meta={(run) => [
                STATUS_WORDS[run.status] ?? run.status,
                run.origin,
                firstLine(run.text),
              ]}
              when={(run) => <Moment at={run.createdAt} />}
              open={(run) => () =>
                onPlace({ opens: opened(opens, RUN_PREFIX + run.name, undefined) })
              }
              action={(run) =>
                run.sourceName ? (
                  <Button
                    variant="quiet"
                    size="icon"
                    aria-label={SETTINGS + " for " + (run.title || run.sourceName)}
                    onClick={() =>
                      onPlace({ opens: opened(opens, slotOf(settings(run)), undefined) })
                    }
                  >
                    <IconSettings aria-hidden />
                  </Button>
                ) : null
              }
            />
            <Pager payload={{ older: held.next_cursor }} onPlace={onPlace} />
          </>
        );
      }}
    </Panel>
  );
}

/** `stops` names this run, so a drawer standing on an older run cannot end the conversation's
 *  newest turn. */
function RunSheet({
  id,
  run,
  opens,
  onPlace,
}: {
  id: string;
  run: Run | null;
  opens: string[];
  onPlace: (place: Placement) => void;
}) {
  const agents = useAgents();
  const member = useMe();
  const shut = () => onPlace({ opens: closed(opens, RUN_PREFIX + id) });
  const [working, setWorking] = useState(true);
  const state = usePanelRead<DetailPayload>(
    run === null ? null : "/objects/" + TURN_KIND + "/" + id + "?agent=" + run.agent,
    0,
    working ? WORKING_MS : RESTING_MS,
  );
  const status = state.phase === "ready" ? said(state.payload.status.status) : null;
  useEffect(() => {
    if (status !== null) setWorking(LIVE.has(status));
  }, [status]);
  const agent = run === null ? undefined : agents.find((entry) => entry.id === run.agent);
  return (
    <Sheet open title={run?.title || RUN} onClose={shut}>
      {run === null || member === null ? (
        <PanelEmpty>{NOT_ON_PAGE}</PanelEmpty>
      ) : agent === undefined ? (
        <PanelEmpty>{NO_AGENT}</PanelEmpty>
      ) : state.phase === "loading" ? (
        <Loading />
      ) : (
        <ChatPane
          agent={agent}
          member={member}
          conversationId={run.conversation}
          readOnly
          stops={id}
          conversationOnly
        />
      )}
    </Sheet>
  );
}

function RecordSheet({
  id,
  opens,
  onPlace,
}: {
  id: string;
  opens: string[];
  onPlace: (place: Placement) => void;
}) {
  const shut = () => onPlace({ opens: closed(opens, id) });
  const held = objectAt(id);
  if (held === null)
    return (
      <Sheet open title={id} onClose={shut}>
        <PanelEmpty>That item is not on this page.</PanelEmpty>
      </Sheet>
    );
  return (
    <ObjectDetail
      agentId={held.agent}
      kind={held.kind}
      name={held.name}
      onOpen={(next) => onPlace({ opens: opened(opens, slotOf(next), id) })}
      onBack={shut}
    />
  );
}

/** Every run of the workspace's scheduled tasks and source triggers, newest first — the turn kind's
 *  fired turns — narrowed to one task or trigger by the filter. A row opens its transcript
 *  read-only in the drawer, where a running turn can be stopped; its gear opens the settings of
 *  what fired it. */
export function TaskRuns({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const scope = place.scope ?? "";
  const opens = place.opens ?? [];
  const [shown, setShown] = useState<Run[]>([]);
  return (
    <>
      <PageToolbar>
        <span className="ml-auto flex shrink-0 items-center gap-sm max-narrow:ml-0">
          <Filter
            scope={scope}
            onPick={(next) => onPlace({ scope: next || undefined, after: undefined, opens: [] })}
          />
        </span>
      </PageToolbar>
      <Section>
        <Runs place={place} onPlace={onPlace} onShown={setShown} />
      </Section>
      {opens.slice(-1).map((id) =>
        id.startsWith(RUN_PREFIX) ? (
          <RunSheet
            key={id}
            id={id.slice(RUN_PREFIX.length)}
            run={shown.find((run) => RUN_PREFIX + run.name === id) ?? null}
            opens={opens}
            onPlace={onPlace}
          />
        ) : (
          <RecordSheet key={id} id={id} opens={opens} onPlace={onPlace} />
        ),
      )}
    </>
  );
}
