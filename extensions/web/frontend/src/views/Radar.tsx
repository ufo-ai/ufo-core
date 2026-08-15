import { Filter } from "@/components/ui/filter";
import { ObjectDetail, ObjectPane, type ObjectAddress } from "@/kernel/objects";
import { Pager, type Placement } from "@/kernel/pager";
import { PageHeader, PageToolbar } from "@/kernel/pane";
import { Panel, PanelBlank, Section, usePanelRead } from "@/kernel/panel";
import { Markdown } from "@/lib/markdown";
import { day } from "@/lib/moments";
import { chatHash } from "@/lib/route";
import { formatSize } from "@/lib/size";

/** What stands on the radar: the feed of what ran on its own leads, and the two kinds of standing
 *  order — a schedule, and a trigger against a source — stay reachable as the families behind it.
 *  One destination because a member asking what happens here without anyone typing asks one
 *  question; the feed answers what it did, the kinds answer what is armed. */
const FAMILIES = [
  { label: "Runs", value: "" },
  { label: "Scheduled", value: "scheduled_task" },
  { label: "Triggers", value: "source_trigger" },
];

const TASK_KIND = "scheduled_task";
const OBJECT_PREFIX = "object/";

type RadarArtifact = {
  filename: string;
  subject: string | null;
  media_type: string;
  size_bytes: number;
  url: string | null;
  preview_url: string | null;
};

type RadarRun = {
  turn_id: string;
  conversation_id: string;
  title: string | null;
  origin: string | null;
  agent_id: string;
  agent_name: string | null;
  fired_at: string;
  status: string;
  task: string | null;
  text: string;
  artifacts: RadarArtifact[];
};

type RadarPayload = { runs: RadarRun[]; older?: string | null; newer?: string | null };

/** A run that ended well needs no mark beside its own reply; every other outcome is stated. */
const STATUS_NOTES: Record<string, string> = {
  failed: "Failed",
  cancelled: "Stopped",
  running: "Running",
  queued: "Running",
  parked: "Waiting",
};

const WORKING = new Set(["running", "queued", "parked"]);

function objectAt(open: string | undefined): ObjectAddress | null {
  if (!open?.startsWith(OBJECT_PREFIX)) return null;
  const rest = open.slice(OBJECT_PREFIX.length);
  const cut = rest.indexOf("/");
  return cut < 0 ? null : { kind: rest.slice(0, cut), name: rest.slice(cut + 1) };
}

/** Every agent's radar, or one agent's. The section names no agent, so its feed answers across the
 *  viewer's whole audience; the agent tab is already headed and named, so it passes no title. A
 *  story's task name opens that task's record here in the pane — `place.agent` remembers whose
 *  namespace the record lives in, since the section's feed crosses agents. */
export function Radar({
  agentId,
  title,
  place,
  onPlace,
}: {
  agentId: string | null;
  title?: string;
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const at = objectAt(place.open);
  const owner = agentId ?? place.agent ?? null;
  if (at !== null && at.name !== null && owner !== null) {
    return (
      <ObjectDetail
        key={owner + "/" + at.kind + "/" + at.name}
        agentId={owner}
        kind={at.kind}
        name={at.name}
        onOpen={(next) => onPlace({ open: OBJECT_PREFIX + next.kind + "/" + (next.name ?? "") })}
        onBack={() => onPlace({ open: undefined, agent: undefined })}
      />
    );
  }
  const family = place.chip ?? "";
  if (family) {
    return (
      <ObjectPane
        key={family}
        agentId={agentId}
        kind={family}
        title={title}
        lead={<Lead value={family} onPlace={onPlace} />}
      />
    );
  }
  return (
    <>
      {title ? <PageHeader title={title} /> : null}
      <PageToolbar>
        <Lead value="" onPlace={onPlace} />
      </PageToolbar>
      <Section>
        <Feed agentId={agentId} place={place} onPlace={onPlace} />
      </Section>
    </>
  );
}

function Lead({ value, onPlace }: { value: string; onPlace: (place: Placement) => void }) {
  return (
    <Filter
      options={FAMILIES}
      value={value}
      onChange={(picked) => onPlace({ chip: picked || undefined, after: undefined })}
      all={false}
    />
  );
}

function Feed({
  agentId,
  place,
  onPlace,
}: {
  agentId: string | null;
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const params = new URLSearchParams();
  if (agentId) params.set("agent", agentId);
  if (place.after) params.set("after", place.after);
  const state = usePanelRead<RadarPayload>(
    "/workspace/radar" + (params.size ? "?" + params.toString() : ""),
  );
  return (
    <Panel state={state} shape="cards">
      {(payload) => {
        if (!payload.runs.length)
          return (
            <PanelBlank body="Each scheduled run reports here: the reply it closed with and the files it shared." />
          );
        return (
          <>
            <div className="flex flex-col gap-4xl">
              {editions(payload.runs).map((edition) => (
                <section key={edition.date} className="flex flex-col">
                  <div className="flex items-center gap-lg">
                    <h2 className="m-0 font-mono text-mono font-normal uppercase text-ink-soft">
                      {edition.date}
                    </h2>
                    <span aria-hidden className="h-px flex-1 bg-edge" />
                  </div>
                  <ol className="m-0 flex list-none flex-col p-0">
                    {edition.runs.map((run) => (
                      <Story key={run.turn_id} run={run} onPlace={onPlace} />
                    ))}
                  </ol>
                </section>
              ))}
            </div>
            <div className="mt-4xl">
              <Pager payload={payload} onPlace={onPlace} />
            </div>
          </>
        );
      }}
    </Panel>
  );
}

/** The page's runs under the calendar day each fired on, in the order the page already holds. */
function editions(runs: RadarRun[]): { date: string; runs: RadarRun[] }[] {
  const grouped: { date: string; runs: RadarRun[] }[] = [];
  for (const run of runs) {
    const date = day(run.fired_at) ?? "";
    const last = grouped[grouped.length - 1];
    if (last && last.date === date) last.runs.push(run);
    else grouped.push({ date, runs: [run] });
  }
  return grouped;
}

/** One run as a story: the headline opens the conversation the run reported into, the byline names
 *  the task that fired it — pressed, it opens that task's record, where the prompt and schedule
 *  are read — beside who ran it and how it ended, the body is the reply, and what it shared stands
 *  under it with its picture where one exists. */
function Story({ run, onPlace }: { run: RadarRun; onPlace: (place: Placement) => void }) {
  const note = STATUS_NOTES[run.status];
  return (
    <li className="flex flex-col gap-sm border-b border-edge py-4xl last:border-b-0">
      <h3 className="m-0 font-display text-title font-normal">
        <a href={chatHash(run.conversation_id)} className="text-ink no-underline hover:underline">
          {run.title || run.task || "Scheduled run"}
        </a>
      </h3>
      <p className="m-0 flex flex-wrap gap-x-lg font-mono text-mono text-ink-soft">
        {run.task ? (
          <button
            type="button"
            onClick={() =>
              onPlace({ open: OBJECT_PREFIX + TASK_KIND + "/" + run.task, agent: run.agent_id })
            }
            className="m-0 border-0 bg-transparent p-0 font-mono text-mono text-ink-soft underline underline-offset-2 hover:text-ink"
          >
            {run.task}
          </button>
        ) : null}
        {[run.agent_name, run.origin].filter(Boolean).map((part) => (
          <span key={part}>{part}</span>
        ))}
        {note ? (
          <span
            className={
              run.status === "failed"
                ? "[color:var(--color-attention-ink)]"
                : WORKING.has(run.status)
                  ? "animate-working"
                  : undefined
            }
          >
            {note}
          </span>
        ) : null}
      </p>
      {run.text ? (
        <div className="line-clamp-6 text-body leading-reading">
          <Markdown text={run.text} />
        </div>
      ) : null}
      {run.artifacts.length ? (
        <ul className="m-0 mt-sm flex list-none flex-wrap gap-lg p-0">
          {run.artifacts.map((artifact) => (
            <li key={artifact.filename}>
              <Shared artifact={artifact} />
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/** A file the run shared: its picture where one exists, else its name and size — either way the
 *  signed link opens it, and a file whose link is not minted is named without one. */
function Shared({ artifact }: { artifact: RadarArtifact }) {
  const card = artifact.preview_url ? (
    <img
      loading="lazy"
      alt={artifact.subject || artifact.filename}
      src={artifact.preview_url}
      className="h-(--size-band) rounded-panel border border-edge object-cover"
    />
  ) : (
    <span className="flex items-baseline gap-sm rounded-panel border border-edge px-lg py-sm">
      <span className="font-mono text-small text-ink">{artifact.filename}</span>
      <span className="font-mono text-mono text-ink-soft">{formatSize(artifact.size_bytes)}</span>
    </span>
  );
  if (!artifact.url) return card;
  return (
    <a
      href={artifact.url}
      target="_blank"
      rel="noopener noreferrer"
      className="block no-underline hover:opacity-muted-soft"
    >
      {card}
    </a>
  );
}
