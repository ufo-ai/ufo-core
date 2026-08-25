// The radar app's page: a static site built with the portal's app kit. Edit this file and redeploy
// to change the page.

import {
  ARTIFACT_TEXT_BYTES,
  AgentIcon,
  Avatar,
  AvatarFallback,
  Button,
  Dialog,
  DialogTrigger,
  FileSheet,
  Header,
  Markdown,
  Moment,
  ObjectDetail,
  Panel,
  PanelBlank,
  PanelEmpty,
  PressRow,
  RebuildDialog,
  Section,
  SectionApp,
  Sheet,
  Waiting,
  agentHash,
  agentName,
  appended,
  beside,
  buttonVariants,
  chatHash,
  closed,
  cn,
  formatSize,
  mountApp,
  objectAt,
  opened,
  sectionHash,
  slackLink,
  slotOf,
  useAgents,
  useCallback,
  useLayoutEffect,
  usePageHead,
  usePanelRead,
  useState,
  useTextArtifact,
} from "ufo/kit";
import type { Crumb, ObjectAddress, Placement, ReactMouseEvent } from "ufo/kit";

const TASK_KIND = "scheduled_task";
const RUN_PREFIX = "run/";
const DONE = "done";

/** What the crumb says a pinned page is until the report standing on it has named itself. The
 *  read may answer no run at all, and the way back off a pin cannot wait on a name that is never
 *  coming. */
const REPORT = "Report";

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
  entry: DigestWritten | null;
  artifacts: RadarArtifact[];
};

/** One finding as the digest writer states it, and — where the report says who did the thing —
 *  the person who did it. */
type DigestPoint = { text: string; actor: string };

/** What the report-digest skill wrote about one report. A report published since the job last ran
 *  carries none yet, and stands on its task's name until it does. */
type DigestWritten = { title: string; summary: string; points: DigestPoint[] };

type ReportRow = {
  name: string;
  agent_id: string;
  conversation: string;
  fired_at: string;
  status: string;
  task: string | null;
  surface: string;
  source: string | null;
  text: string;
  entry: DigestWritten | null;
  artifacts: RadarArtifact[];
};

type ReportsPayload = { objects: ReportRow[]; next_cursor: string | null };

/** One report as the detail route answers it: the listing row's fields ride in `status`, beside
 *  the name the permalink carries. The detail resolves the turn directly — a report older than
 *  the feed's own window still answers its permalink — and its row names the firing agent. */
type ReportDetail = { name: string; status: Omit<ReportRow, "name"> };

/** One listing row as the feed reads it: the report kind's own fields, folded back into the run
 *  the stories are written over — the digest rides whole in the row's `entry` field, because a
 *  flat `summary` would collide with the listing row's own. */
function toRun(row: ReportRow): RadarRun {
  return {
    turn_id: row.name,
    conversation_id: row.conversation,
    agent_id: row.agent_id,
    fired_at: row.fired_at,
    status: row.status,
    task: row.task,
    surface: row.surface,
    source: row.source,
    text: row.text,
    entry: row.entry,
    artifacts: row.artifacts,
  };
}

/** A run that ended well needs no mark beside its own reply; the other endings are stated. */
const STATUS_NOTES: Record<string, string> = {
  failed: "Failed",
  cancelled: "Stopped",
};

function Radar({
  title,
  crumb,
  place,
  onPlace,
}: {
  title: string;
  crumb?: Crumb;
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const [named, setNamed] = useState<{ run: string; name: string } | null>(null);
  const name = useCallback((run: string, name: string) => setNamed({ run, name }), []);
  const opens = place.opens ?? [];
  const pin = opens.find((id) => id.startsWith(RUN_PREFIX) && id !== RUN_PREFIX);
  const page = pin === undefined ? title : named?.run === pin ? named.name : REPORT;
  const band = usePageHead(
    pin === undefined ? (
      <Header pinned heading={1} title={page} acts={<RebuildEntries />} />
    ) : (
      <Header
        pinned
        heading={1}
        crumb={crumb}
        title={page}
        closes={page}
        onClose={() => onPlace({ opens: closed(opens, pin) })}
      />
    ),
  );
  return (
    <>
      {band}
      <Section>
        <Feed place={place} pin={pin} onPlace={onPlace} onName={name} />
      </Section>
      {opens
        .filter((id) => id !== pin)
        .slice(-1)
        .map((id) => (
          <RecordSlot
            key={id}
            id={id}
            held={objectAt(id)}
            opens={opens}
            onPlace={onPlace}
          />
        ))}
    </>
  );
}

/** The feed's one act: the entries under the reports, written again. What a report says is the
 *  report's own and is never touched — this reaches the title and the lines the digest job wrote
 *  over it, which that job can write again from the report it read the first time. The window is
 *  the job's, and the dialog says so, because a report the job will never read again would lose its
 *  entry rather than gain a better one. */
function RebuildEntries() {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button size="bar">Rebuild entries</Button>
      </DialogTrigger>
      <RebuildDialog title="Rebuild Entries" action="Rebuild entries" verb="rebuild_reports">
        <p className="m-0">
          Every report published in the last seven days is read again, and the title and lines
          standing over it are written from scratch.
        </p>
        <p className="m-0">
          A report older than seven days keeps the entry it has. The digest job does not read that
          far back.
        </p>
      </RebuildDialog>
    </Dialog>
  );
}

function RecordSlot({
  id,
  held,
  opens,
  onPlace,
}: {
  id: string;
  held: ObjectAddress | null;
  opens: string[];
  onPlace: (place: Placement) => void;
}) {
  const shut = () => onPlace({ opens: closed(opens, id) });
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

/** The feed, or — when the place carries a run's own address, which is what a story's dateline
 *  links — the one story that address names, with the whole feed one press away in the crumb over
 *  it. A pinned page that answers no run says so: the run is gone or was never this reader's to
 *  read. */
function Feed({
  place,
  pin,
  onPlace,
  onName,
}: {
  place: Placement;
  pin?: string;
  onPlace: (place: Placement) => void;
  onName: (run: string, name: string) => void;
}) {
  const opens = place.opens ?? [];
  const agents = useAgents();
  const pinned = pin === undefined ? null : pin.slice(RUN_PREFIX.length);
  const after = place.after ?? "";
  const owner = agents.find((agent) => agent.app === "radar") ?? agents[0];
  const detail = usePanelRead<ReportDetail>(
    pinned && owner ? "/objects/report/" + pinned + "?agent=" + owner.id : null,
  );
  const feedParams = new URLSearchParams({ order_by: "fired_at", order: "desc" });
  if (after) feedParams.set("cursor", after);
  const state = usePanelRead<ReportsPayload>(
    pinned ? null : "/objects/report?" + feedParams.toString(),
  );
  if (pinned) {
    if (detail.phase === "failed" && detail.status === 404)
      return <PanelBlank body="This report does not exist or is not shared with you." />;
    return (
      <Panel state={detail} shape="cards">
        {(payload) => (
          <>
            <ol className="m-0 flex list-none flex-col p-0">
              <Story
                run={toRun({
                  ...payload.status,
                  name: payload.name,
                  agent_id: payload.status.agent_id || (owner?.id ?? ""),
                })}
                opens={opens}
                from={pin}
                onPlace={onPlace}
                onName={onName}
              />
            </ol>
            <ReadNext pinned={pinned} />
          </>
        )}
      </Panel>
    );
  }
  return (
    <Panel state={state} shape="cards">
      {(payload) => {
        const runs = payload.objects.map(toRun);
        if (!runs.length && !after)
          return (
            <PanelBlank body="Each scheduled run reports here: the reply it closed with and the files it shared." />
          );
        return (
          <>
            <ol className="m-0 flex list-none flex-col p-0">
              {runs.map((run) => (
                <Entry key={run.turn_id} run={run} opens={opens} from={pin} onPlace={onPlace} />
              ))}
            </ol>
            {payload.next_cursor || after ? (
              <div className="flex gap-xs">
                {payload.next_cursor ? (
                  <button
                    className={cn(buttonVariants({ variant: "row" }))}
                    onClick={() => onPlace({ after: payload.next_cursor ?? undefined })}
                  >
                    Older reports
                  </button>
                ) : null}
                {after ? (
                  <button
                    className={cn(buttonVariants({ variant: "row" }))}
                    onClick={() => onPlace({ after: undefined })}
                  >
                    Newest reports
                  </button>
                ) : null}
              </div>
            ) : null}
          </>
        );
      }}
    </Panel>
  );
}

/** How many reports stand under one, offered as what to read next. Enough that the feed is worth
 *  reaching from here, few enough that they read as a postscript to the report rather than as the
 *  feed printed twice. */
const READ_NEXT = 3;

/** What to read after this report: the reports either side of it in the feed, each named the way the
 *  feed names it. A report is the end of a page, and a member who read to the end of one is deciding
 *  what to read next, not whether to go back — the way back stands in the crumb at the top, where
 *  they came in.
 *
 *  The whole feed is read for this, not the one run the page is pinned to, so the page holds a
 *  second read of the same projection. A reader who reaches the foot of a document has waited out
 *  the document; the list under it costs them nothing they were waiting on. */
function ReadNext({ pinned }: { pinned: string }) {
  const state = usePanelRead<ReportsPayload>("/objects/report?order_by=fired_at&order=desc");
  const agents = useAgents();
  if (state.phase !== "ready") return null;
  const rest = state.payload.objects
    .map(toRun)
    .filter((run) => run.turn_id !== pinned)
    .slice(0, READ_NEXT);
  if (!rest.length) return null;
  return (
    <section className="mt-6xl flex flex-col gap-md">
      <h2 className="m-0 text-subtitle font-medium">More reports</h2>
      <div className="flex flex-col">
        {rest.map((run) => {
          const agent = agents.find((entry) => entry.id === run.agent_id);
          return (
            <PressRow
              key={run.turn_id}
              href={sectionHash("radar", { opens: [RUN_PREFIX + run.turn_id] })}
              glyph={
                <Avatar>
                  <AvatarFallback>
                    <AgentIcon name={agent?.icon ?? "propylon"} />
                  </AvatarFallback>
                </Avatar>
              }
              title={run.entry?.title ?? run.task ?? "Scheduled run"}
              body={run.entry?.summary ?? ""}
            />
          );
        })}
      </div>
    </section>
  );
}

/** Which picture stands for a run. Only a file with a rendered preview can stand for one at all —
 *  the feed shows a picture or nothing, never a frame around a name. Among those, a picture the run
 *  drew — a chart, a capture — says at a glance what the report is about, while the first page of a
 *  document says only that a document exists, because every page of prose crops to the same grey
 *  band. So a raster the run shared outranks a rendered page whatever order the two were shared in,
 *  and only a run that drew nothing is represented by its paperwork. */
function cover(artifacts: RadarArtifact[]): RadarArtifact | null {
  const pictures = artifacts.filter((artifact) => artifact.preview_url !== null);
  return (
    pictures.find((artifact) => artifact.media_type.startsWith("image/")) ?? pictures[0] ?? null
  );
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

/** One report on the rail: what it found, in the shortest form that earns a press. The app's own
 *  mark is the node, so which app filed it is read before the entry is, and the rail runs between
 *  the marks and stops under the last. The whole entry opens the report it describes. */
function Entry({
  run,
  opens,
  from,
  onPlace,
}: {
  run: RadarRun;
  opens: string[];
  from?: string;
  onPlace: (place: Placement) => void;
}) {
  const agent = useAgents().find((entry) => entry.id === run.agent_id);
  const picture = cover(run.artifacts);
  const note = STATUS_NOTES[run.status];
  const heading = run.entry?.title ?? run.task ?? "Scheduled run";
  /** A run that did not end well says why, whatever else was written about it: the reason it
   *  stopped is the whole of what the member can act on, and an entry drawn over the partial
   *  report it left would read as though the run had delivered. */
  const summary = run.status === DONE ? (run.entry?.summary ?? null) : run.text || null;
  return (
    <li className="group/entry flex gap-lg">
      <div className="flex flex-col items-center gap-sm">
        <Avatar>
          <AvatarFallback>
            <AgentIcon name={agent?.icon ?? "propylon"} />
          </AvatarFallback>
        </Avatar>
        <span aria-hidden className="w-px flex-1 bg-edge group-last/entry:hidden" />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-sm pb-6xl group-last/entry:pb-0">
        <p className="m-0 truncate font-mono text-small tabular-nums text-ink-soft">
          <Moment at={run.fired_at} />
          {agent ? <span>{" \u00b7 " + agentName(agent.name)}</span> : null}
          {run.task ? " \u00b7 " : null}
          {run.task ? (
            <TaskName
              task={run.task}
              agentId={run.agent_id}
              opens={opens}
              from={from}
              onPlace={onPlace}
              className="m-0 border-0 p-0 text-left font-mono text-inherit hover:underline"
            />
          ) : null}
          {note ? (
            <span
              className={run.status === "failed" ? "[color:var(--color-attention-ink)]" : undefined}
            >
              {" \u00b7 " + note}
            </span>
          ) : null}
        </p>
        <a
          href={sectionHash("radar", { opens: [RUN_PREFIX + run.turn_id] })}
          className="flex items-start gap-xl text-inherit no-underline"
        >
          <div className="flex min-w-0 flex-1 flex-col gap-sm">
            <h3 className="m-0 line-clamp-2 text-subtitle font-medium text-ink group-hover/entry:underline">
              {heading}
            </h3>
            {summary ? <p className="m-0 line-clamp-2 text-ui text-ink-soft">{summary}</p> : null}
            {run.entry?.points.length ? (
              <ul className="m-0 flex list-none flex-col gap-2xs p-0">
                {run.entry.points.map((made, at) => (
                  <li key={run.turn_id + "/" + at} className="flex items-baseline gap-sm text-ui">
                    <span aria-hidden className="text-ink-faint">
                      —
                    </span>
                    <span className="min-w-0 flex-1 truncate text-ink">{made.text}</span>
                    {made.actor ? (
                      <span className="shrink-0 whitespace-nowrap text-small text-ink-soft">
                        {made.actor}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
          {picture ? (
            <img
              loading="lazy"
              alt=""
              src={picture.preview_url ?? ""}
              className="size-(--size-digest-picture) shrink-0 rounded-panel border border-edge object-cover object-top-left"
            />
          ) : null}
        </a>
      </div>
    </li>
  );
}

/** One run as a story: the report it published is the headline, so the story is titled the way the
 *  document titles itself and the page's own heading face carries it. The band over the page states
 *  that same name in its crumb, and the two stand together on purpose — the band gives a name one
 *  line and the measure the crumb leaves it, and a report titles itself in prose that will not fit
 *  there, so the body is where the member reads it whole. Only the crumb's leaf is a heading of the
 *  page, so the name is stated twice and headed once, and the document's own title line is dropped
 *  from the body so the report never says it three times.
 *
 *  Under the heading stands the byline — the agent the task belongs to, reached at its page, and the
 *  task itself, pressed to open the record where the prompt and schedule are read — and under that
 *  the dateline of ways out: when it ran, the conversation it reported into, the thread on the
 *  surface it came from, and any outcome. The body is what the run made, never what it said: files
 *  with their pictures where one exists, markdown documents read inline. Only a run that did not end
 *  well speaks in text, because a failure explains itself; the reply a successful run posted lives
 *  in its conversation, one link away.
 *
 *  A run that published no document, or one whose document opens on no title, is headed by the task
 *  that fired it — every story states what it is before it states what it did. */
function Story({
  run,
  opens,
  from,
  onPlace,
  onName,
}: {
  run: RadarRun;
  opens: string[];
  from?: string;
  onPlace: (place: Placement) => void;
  onName: (run: string, name: string) => void;
}) {
  const [title, setTitle] = useState<string | null>(null);
  const agent = useAgents().find((entry) => entry.id === run.agent_id);
  const note = STATUS_NOTES[run.status];
  const thread = slackLink(run.surface, run.source);
  const documents = run.artifacts.filter(isDocument);
  const files = run.artifacts.filter((artifact) => !isDocument(artifact));
  const heading = title ?? run.task ?? "Scheduled run";
  const out =
    "text-inherit no-underline hover:underline focus-visible:underline";
  useLayoutEffect(() => {
    onName(RUN_PREFIX + run.turn_id, heading);
  }, [onName, run.turn_id, heading]);
  return (
    <li className="flex flex-col gap-sm border-b border-edge pb-4xl last:border-b-0">
      <h3 className="m-0 text-title font-medium">{heading}</h3>
      {agent || run.task ? (
        <p className="m-0 text-ui text-ink-soft">
          by{" "}
          {agent ? (
            <>
              <a href={agentHash(agent.id)} className={out}>
                {agent.name}
              </a>{" "}
            </>
          ) : null}
          {run.task ? (
            <TaskName
              task={run.task}
              agentId={run.agent_id}
              opens={opens}
              from={from}
              onPlace={onPlace}
              className="m-0 border-0 p-0 text-left text-inherit hover:underline"
            />
          ) : null}
        </p>
      ) : null}
      <p className="m-0 flex flex-wrap gap-x-lg font-mono text-mono text-ink-soft">
        <a href={sectionHash("radar", { opens: [RUN_PREFIX + run.turn_id] })} className={out}>
          <Moment at={run.fired_at} />
        </a>
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
      {documents.map((artifact, at) => (
        <Report
          key={artifact.filename}
          artifact={artifact}
          heading={heading}
          onTitle={at === 0 ? setTitle : undefined}
        />
      ))}
    </li>
  );
}

function TaskName({
  task,
  agentId,
  opens,
  from,
  onPlace,
  className,
}: {
  task: string;
  agentId: string;
  opens: string[];
  from?: string;
  onPlace: (place: Placement) => void;
  className: string;
}) {
  const id = slotOf({ agent: agentId, kind: TASK_KIND, name: task });
  const standing = opens.includes(id);
  const press = (event: ReactMouseEvent<HTMLButtonElement>) =>
    onPlace({ opens: beside(event) ? appended(opens, id) : opened(opens, id, from) });
  return (
    <button
      type="button"
      aria-current={standing}
      onClick={press}
      onAuxClick={(event) => {
        if (beside(event)) press(event);
      }}
      className={cn(className, standing ? "-mx-xs rounded-control bg-fill px-xs" : "bg-transparent")}
    >
      {task}
    </button>
  );
}

/** A document's own title and the body under it: a report opens with the one `# Title` line that
 *  names it, which the story states as its heading instead. */
function titled(text: string): { title: string | null; body: string } {
  const opening = /^\s*#[ \t]+(\S.*?)[ \t]*(?:\n|$)/.exec(text);
  if (!opening) return { title: null, body: text };
  return { title: opening[1], body: text.slice(opening[0].length) };
}

/** The report the run published, read as the story's own body: a member standing on one report's own
 *  address came for that report, so the document flows whole down the page with nothing to press —
 *  neither a fold to open nor a box that scrolls inside a page that scrolls, which would trap the
 *  wheel over the very thing the member came to read. The title line is dropped where the story
 *  already stands under it, and a second document — which titles nothing above it — keeps its own.
 *  The story is told the title before the frame is painted, so no reader ever catches a report
 *  saying its own name twice. */
function Report({
  artifact,
  heading,
  onTitle,
}: {
  artifact: RadarArtifact;
  heading: string;
  onTitle?: (title: string) => void;
}) {
  const { body, bounded, message } = useTextArtifact(artifact.url);
  const read = titled(body ?? "");

  useLayoutEffect(() => {
    if (read.title !== null) onTitle?.(read.title);
  }, [onTitle, read.title]);

  if (message) return <div className="font-mono text-small text-ink-soft">{message}</div>;
  if (body === null)
    return (
      <div>
        <Waiting />
      </div>
    );
  return (
    <>
      <div className="text-body leading-reading">
        <Markdown text={read.title === heading ? read.body : body} />
      </div>
      {bounded ? (
        <div className="font-mono text-small text-ink-soft">
          First {formatSize(ARTIFACT_TEXT_BYTES)} shown.
        </div>
      ) : null}
    </>
  );
}

/** A file the run shared: its picture where one exists, else its name and size — pressing either
 *  opens the file full beside the feed with its download, and a file whose link is not minted is
 *  named without one. */
function Shared({ artifact }: { artifact: RadarArtifact }) {
  const [open, setOpen] = useState(false);
  const card = artifact.preview_url ? (
    <img
      loading="lazy"
      alt={artifact.subject || artifact.filename}
      src={artifact.preview_url}
      className="size-(--size-thumbnail) rounded-panel border border-edge object-cover object-top-left"
    />
  ) : (
    <span className="flex items-baseline gap-sm rounded-panel border border-edge px-lg py-sm">
      <span className="font-mono text-small text-ink">{artifact.filename}</span>
      <span className="font-mono text-mono text-ink-soft">{formatSize(artifact.size_bytes)}</span>
    </span>
  );
  if (!artifact.url) return card;
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="block cursor-pointer border-0 bg-transparent p-0 text-left hover:opacity-muted-soft"
      >
        {card}
      </button>
      {open ? <FileSheet file={artifact} onClose={() => setOpen(false)} /> : null}
    </>
  );
}

mountApp(document.getElementById("root")!, (init) => (
  <SectionApp
    tab="radar"
    init={init}
    view={{
      label: "Radar",
      remountOnPlace: false,
      ownsHeader: true,
      render: (place, onPlace) => (
        <Radar title="Radar" crumb={init.crumb} place={place} onPlace={onPlace} />
      ),
    }}
  />
));
