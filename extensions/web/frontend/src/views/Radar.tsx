import { Filter } from "@/components/ui/filter";
import { ArtifactText } from "@/kernel/artifact";
import { useBeside } from "@/kernel/beside";
import { ObjectDetail, ObjectPane, type ObjectAddress } from "@/kernel/objects";
import { Pager, type Placement } from "@/kernel/pager";
import { PageHeader, PageToolbar } from "@/kernel/pane";
import { Panel, PanelBlank, Section, usePanelRead } from "@/kernel/panel";
import { slackLink } from "@/lib/audience";
import { Markdown } from "@/lib/markdown";
import { Moment, day } from "@/lib/moments";
import { chatHash } from "@/lib/route";
import { formatSize } from "@/lib/size";

/** What stands on the radar: the feed of what ran on its own leads, and the two kinds of standing
 *  order — a schedule, and a trigger against a source — stay reachable as the families behind it.
 *  One destination because a member asking what happens here without anyone typing asks one
 *  question; the feed answers what it did, the kinds answer what is armed. */
const FAMILIES = [
  { label: "Reports", value: "" },
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
  agent_id: string;
  fired_at: string;
  status: string;
  task: string | null;
  surface: string;
  source: string | null;
  text: string;
  artifacts: RadarArtifact[];
};

type RadarPayload = { runs: RadarRun[]; older?: string | null; newer?: string | null };

/** A run that ended well needs no mark beside its own reply; the other endings are stated. */
const STATUS_NOTES: Record<string, string> = {
  failed: "Failed",
  cancelled: "Stopped",
};

function objectAt(open: string | undefined): ObjectAddress | null {
  if (!open?.startsWith(OBJECT_PREFIX)) return null;
  const rest = open.slice(OBJECT_PREFIX.length);
  const cut = rest.indexOf("/");
  return cut < 0 ? null : { kind: rest.slice(0, cut), name: rest.slice(cut + 1) };
}

/** The workspace's radar: the feed answers across the viewer's whole audience. A story's task name
 *  opens that task's record beside the feed — `place.agent` remembers whose namespace the record
 *  lives in, since the feed crosses agents. Another panel taking the pane's one column clears the
 *  record out of the place as displaced, so the route stays on the screen the member is standing
 *  on rather than stepping back off the panel they just opened. */
export function Radar({
  title,
  place,
  onPlace,
}: {
  title?: string;
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const at = objectAt(place.open);
  const owner = place.agent ?? null;
  const detail = useBeside(
    at !== null && at.name !== null && owner !== null ? (
      <ObjectDetail
        key={owner + "/" + at.kind + "/" + at.name}
        agentId={owner}
        kind={at.kind}
        name={at.name}
        onOpen={(next) => onPlace({ open: OBJECT_PREFIX + next.kind + "/" + (next.name ?? "") })}
        onBack={() => onPlace({ open: undefined, agent: undefined })}
      />
    ) : null,
    () => onPlace({ open: undefined, agent: undefined, displaced: true }),
  );
  const family = place.chip ?? "";
  if (family) {
    return (
      <>
        <ObjectPane
          key={family}
          agentId={null}
          kind={family}
          title={title}
          lead={<Lead value={family} onPlace={onPlace} />}
        />
        {detail}
      </>
    );
  }
  return (
    <>
      {title ? <PageHeader title={title} /> : null}
      <PageToolbar>
        <Lead value="" onPlace={onPlace} />
      </PageToolbar>
      <Section>
        <Feed place={place} onPlace={onPlace} />
      </Section>
      {detail}
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
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const params = new URLSearchParams();
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

/** A markdown file the run shared is the run's own document: it reads inline as the story rather
 *  than standing as a chip the member must download to open. One whose link is not minted cannot
 *  be read here and stays a chip. */
function isDocument(artifact: RadarArtifact): boolean {
  return (
    artifact.url !== null &&
    (artifact.media_type === "text/markdown" || artifact.filename.endsWith(".md"))
  );
}

/** One run as a story: the task that fired it is the headline — pressed, it opens that task's
 *  record, where the prompt and schedule are read — and under it the dateline of ways out: when
 *  it ran, the conversation it reported into, the thread on the surface it came from, and any
 *  outcome. The body is what the run made, never what it said: files with their pictures where
 *  one exists, markdown documents read inline whole. Only a run that did not end well speaks in
 *  text, because a failure explains itself; the reply a successful run posted lives in its
 *  conversation, one link away. */
function Story({
  run,
  onPlace,
}: {
  run: RadarRun;
  onPlace: (place: Placement) => void;
}) {
  const note = STATUS_NOTES[run.status];
  const thread = slackLink(run.surface, run.source);
  const documents = run.artifacts.filter(isDocument);
  const files = run.artifacts.filter((artifact) => !isDocument(artifact));
  const pages = documents.map((artifact) => (
    <ArtifactText
      key={artifact.filename}
      url={artifact.url}
      name={artifact.filename}
      mediaType="text/markdown"
      display="frame"
    />
  ));
  const out =
    "text-inherit no-underline hover:underline focus-visible:underline";
  return (
    <li className="flex flex-col gap-sm border-b border-edge py-4xl last:border-b-0">
      <h3 className="m-0 font-display text-title font-normal">
        {run.task ? (
          <button
            type="button"
            onClick={() =>
              onPlace({ open: OBJECT_PREFIX + TASK_KIND + "/" + run.task, agent: run.agent_id })
            }
            className="m-0 border-0 bg-transparent p-0 text-left font-display text-title font-normal text-ink hover:underline"
          >
            {run.task}
          </button>
        ) : (
          "Scheduled run"
        )}
      </h3>
      <p className="m-0 flex flex-wrap gap-x-lg font-mono text-mono text-ink-soft">
        <Moment at={run.fired_at} />
        <a href={chatHash(run.conversation_id)} className={out}>
          Conversation
        </a>
        {thread ? (
          <a href={thread} target="_blank" rel="noopener noreferrer" className={out}>
            Slack <span aria-hidden>↗</span>
          </a>
        ) : null}
        {note ? (
          <span
            className={
              run.status === "failed" ? "[color:var(--color-attention-ink)]" : undefined
            }
          >
            {note}
          </span>
        ) : null}
      </p>
      {files.length ? (
        <ul className="m-0 flex list-none flex-wrap gap-lg p-0">
          {files.map((artifact) => (
            <li key={artifact.filename}>
              <Shared artifact={artifact} />
            </li>
          ))}
        </ul>
      ) : null}
      {run.status !== "done" && run.text ? (
        <div className="text-body leading-reading">
          <Markdown text={run.text} />
        </div>
      ) : null}
      {pages}
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
