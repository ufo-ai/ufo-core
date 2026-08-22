import { useLayoutEffect, useState } from "react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button, buttonVariants } from "@/components/ui/button";
import { Dialog, DialogTrigger } from "@/components/ui/dialog";
import { PressRow } from "@/components/ui/pressrow";
import { Sheet } from "@/components/ui/sheet";
import { ARTIFACT_TEXT_BYTES, useTextArtifact } from "@/kernel/artifact";
import { useBeside } from "@/kernel/beside";
import { ObjectDetail, type ObjectAddress } from "@/kernel/objects";
import { Pager, type Placement } from "@/kernel/pager";
import { PageHeader } from "@/kernel/pane";
import { Panel, PanelBlank, Section, usePanelRead } from "@/kernel/panel";
import { RebuildDialog } from "@/kernel/rebuild";
import { slackLink } from "@/lib/audience";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { cn } from "@/lib/cn";
import { useAgents } from "@/lib/mainAgent";
import { Markdown } from "@/lib/markdown";
import { Moment } from "@/lib/moments";
import { agentHash, chatHash, sectionHash } from "@/lib/route";
import { formatSize } from "@/lib/size";

const TASK_KIND = "scheduled_task";
const OBJECT_PREFIX = "object/";
const RUN_PREFIX = "run/";
const DONE = "done";

export type RadarArtifact = {
  filename: string;
  subject: string | null;
  media_type: string;
  size_bytes: number;
  url: string | null;
  preview_url: string | null;
};

export type RadarRun = {
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
export type DigestPoint = { text: string; actor: string };

/** What the report-digest skill wrote about one report. A report published since the job last ran
 *  carries none yet, and stands on its task's name until it does. */
export type DigestWritten = { title: string; summary: string; points: DigestPoint[] };

export type RadarPayload = { runs: RadarRun[]; older?: string | null; newer?: string | null };

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

/** The workspace's radar: the feed of what ran on its own, answered across the viewer's whole
 *  audience. What is armed to run is the tasks screen's answer, not this one — a member here is
 *  reading what happened. A story's task name opens that task's record in the place the report
 *  stood, since the place holds one thing at a time; `place.agent` remembers whose namespace the
 *  record lives in, because the feed crosses agents. */
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
  );
  const pinned = place.open?.startsWith(RUN_PREFIX) ? place.open.slice(RUN_PREFIX.length) : null;
  return (
    <>
      {pinned ? (
        <Back label={title ?? "Radar"} onGo={() => onPlace({ open: undefined })} />
      ) : title ? (
        <PageHeader title={title} action={<RebuildEntries />} />
      ) : null}
      <Section>
        <Feed place={place} onPlace={onPlace} />
      </Section>
      {detail}
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

/** The way back to the list a report was opened from, standing over the report rather than under
 *  it: a member who wants the feed again should not have to read to the end of a document to find
 *  it. The report names itself below — the page is headed by what it is, and this is the path it
 *  was reached by. */
function Back({ label, onGo }: { label: string; onGo: () => void }) {
  return (
    <button
      type="button"
      aria-label={"Back to " + label}
      onClick={onGo}
      className={cn(
        "m-0 shrink-0 self-start border-0 bg-transparent p-0 text-left text-body text-ink-soft",
        "transition-colors duration-100 ease-control hover:text-ink",
      )}
    >
      {label}
    </button>
  );
}

/** The feed, or — when the place carries a run's own address, which is what a story's dateline
 *  links — the one story that address names. The way back to the whole feed stands over the story
 *  rather than in here. A pinned page that answers no run says so: the run is gone or was never
 *  this reader's to read. */
function Feed({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const pinned = place.open?.startsWith(RUN_PREFIX)
    ? place.open.slice(RUN_PREFIX.length)
    : null;
  const params = new URLSearchParams();
  if (pinned) params.set("turn", pinned);
  else if (place.after) params.set("after", place.after);
  const state = usePanelRead<RadarPayload>(
    "/workspace/radar" + (params.size ? "?" + params.toString() : ""),
  );
  return (
    <Panel state={state} shape="cards">
      {(payload) => {
        if (!payload.runs.length)
          return (
            <PanelBlank
              body={
                pinned
                  ? "This report does not exist or is not shared with you."
                  : "Each scheduled run reports here: the reply it closed with and the files it shared."
              }
            />
          );
        if (pinned)
          return (
            <>
              <ol className="m-0 flex list-none flex-col p-0">
                {payload.runs.map((run) => (
                  <Story key={run.turn_id} run={run} onPlace={onPlace} />
                ))}
              </ol>
              <ReadNext pinned={pinned} />
            </>
          );
        return (
          <>
            <ol className="m-0 flex list-none flex-col p-0">
              {payload.runs.map((run) => (
                <Entry key={run.turn_id} run={run} onPlace={onPlace} />
              ))}
            </ol>
            <div className="mt-4xl">
              <Pager payload={payload} onPlace={onPlace} />
            </div>
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
 *  what to read next, not whether to go back — the way back stands at the top, where they came in.
 *
 *  The whole feed is read for this, not the one run the page is pinned to, so the page holds a
 *  second read of the same projection. A reader who reaches the foot of a document has waited out
 *  the document; the list under it costs them nothing they were waiting on. */
function ReadNext({ pinned }: { pinned: string }) {
  const state = usePanelRead<RadarPayload>("/workspace/radar");
  const agents = useAgents();
  if (state.phase !== "ready") return null;
  const rest = state.payload.runs.filter((run) => run.turn_id !== pinned).slice(0, READ_NEXT);
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
              href={sectionHash("radar", { open: RUN_PREFIX + run.turn_id })}
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
  onPlace,
}: {
  run: RadarRun;
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
            <button
              type="button"
              onClick={() =>
                onPlace({ open: OBJECT_PREFIX + TASK_KIND + "/" + run.task, agent: run.agent_id })
              }
              className="m-0 border-0 bg-transparent p-0 text-left font-mono text-inherit hover:underline"
            >
              {run.task}
            </button>
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
          href={sectionHash("radar", { open: RUN_PREFIX + run.turn_id })}
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
 *  document titles itself and the page's own heading face carries it. Under it stands the byline —
 *  the agent the task belongs to, reached at its page, and the task itself, pressed to open the
 *  record where the prompt and schedule are read — and under that the dateline of ways out: when it
 *  ran, the conversation it reported into, the thread on the surface it came from, and any outcome.
 *  The body is what the run made, never what it said: files with their pictures where one exists,
 *  markdown documents read inline. Only a run that did not end well speaks in text, because a
 *  failure explains itself; the reply a successful run posted lives in its conversation, one link
 *  away.
 *
 *  A run that published no document, or one whose document opens on no title, is headed by the task
 *  that fired it — every story states what it is before it states what it did. The heading is the
 *  page's own, because a pinned story is the whole of the page it stands on. */
function Story({
  run,
  onPlace,
}: {
  run: RadarRun;
  onPlace: (place: Placement) => void;
}) {
  const [reportTitle, setReportTitle] = useState<string | null>(null);
  const agent = useAgents().find((entry) => entry.id === run.agent_id);
  const note = STATUS_NOTES[run.status];
  const thread = slackLink(run.surface, run.source);
  const documents = run.artifacts.filter(isDocument);
  const files = run.artifacts.filter((artifact) => !isDocument(artifact));
  const heading = reportTitle ?? run.task ?? "Scheduled run";
  const out =
    "text-inherit no-underline hover:underline focus-visible:underline";
  return (
    <li className="flex flex-col gap-sm border-b border-edge pb-4xl last:border-b-0">
      <h1 className="m-0 text-title font-medium">{heading}</h1>
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
            <button
              type="button"
              onClick={() =>
                onPlace({ open: OBJECT_PREFIX + TASK_KIND + "/" + run.task, agent: run.agent_id })
              }
              className="m-0 border-0 bg-transparent p-0 text-left text-inherit hover:underline"
            >
              {run.task}
            </button>
          ) : null}
        </p>
      ) : null}
      <p className="m-0 flex flex-wrap gap-x-lg font-mono text-mono text-ink-soft">
        <a href={sectionHash("radar", { open: RUN_PREFIX + run.turn_id })} className={out}>
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
          onTitle={at === 0 ? setReportTitle : undefined}
        />
      ))}
    </li>
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
  if (body === null) return <div>Loading…</div>;
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
      {open ? <FileSheet artifact={artifact} onClose={() => setOpen(false)} /> : null}
    </>
  );
}

/** The pressed file at full size: its picture — the file itself when it is an image, the first
 *  rendered page when it is a document — under its name and the download that fetches the file
 *  itself. A file with no picture is stated as such; the download still answers. */
function FileSheet({ artifact, onClose }: { artifact: RadarArtifact; onClose: () => void }) {
  const meta = [artifact.subject, artifact.media_type, formatSize(artifact.size_bytes)]
    .filter((part) => part)
    .join(" · ");
  return (
    <Sheet
      open
      onClose={onClose}
      title={artifact.filename}
      actions={
        <a
          href={artifact.url ?? ""}
          download={artifact.filename}
          className={cn(buttonVariants({ variant: "send" }), "shrink-0 no-underline")}
        >
          Download
        </a>
      }
    >
      <div className="font-mono text-small text-ink-soft">{meta}</div>
      {artifact.preview_url || artifact.media_type.startsWith("image/") ? (
        <FullPicture artifact={artifact} />
      ) : (
        <div className="font-mono text-small text-ink-soft">
          No preview for this file type. Download it to open it.
        </div>
      )}
    </Sheet>
  );
}

function FullPicture({ artifact }: { artifact: RadarArtifact }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div className="font-mono text-small text-ink-soft">
        The image did not load. Its link may have expired — reload the page.
      </div>
    );
  }
  return (
    <img
      alt={artifact.subject || artifact.filename}
      src={artifact.preview_url ?? artifact.url ?? ""}
      onError={() => setFailed(true)}
      className="max-h-(--media-tall) max-w-full object-contain"
    />
  );
}
