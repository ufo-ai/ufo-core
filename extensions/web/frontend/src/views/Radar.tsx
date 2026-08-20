import { useLayoutEffect, useState } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Filter } from "@/components/ui/filter";
import { Reveal } from "@/components/ui/reveal";
import { Sheet } from "@/components/ui/sheet";
import { ARTIFACT_TEXT_BYTES, useTextArtifact } from "@/kernel/artifact";
import { useBeside } from "@/kernel/beside";
import { ObjectDetail, ObjectPane, type ObjectAddress } from "@/kernel/objects";
import { Pager, type Placement } from "@/kernel/pager";
import { PageHeader, PageToolbar } from "@/kernel/pane";
import { Panel, PanelBlank, Section, usePanelRead } from "@/kernel/panel";
import { slackLink } from "@/lib/audience";
import { cn } from "@/lib/cn";
import { useAgents } from "@/lib/mainAgent";
import { Markdown } from "@/lib/markdown";
import { Moment, day } from "@/lib/moments";
import { agentHash, chatHash, sectionHash } from "@/lib/route";
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
const RUN_PREFIX = "run/";

type RadarArtifact = {
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
  artifacts: RadarArtifact[];
};

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

/** The feed, or — when the place carries a run's own address, which is what a story's dateline
 *  links — the one story that address names, with the whole feed one press away. A pinned page
 *  that answers no run says so: the run is gone or was never this reader's to read. */
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
        const back = pinned ? (
          <Button variant="row" onClick={() => onPlace({ open: undefined })}>
            All reports
          </Button>
        ) : null;
        if (!payload.runs.length)
          return (
            <>
              <PanelBlank
                body={
                  pinned
                    ? "This report does not exist or is not shared with you."
                    : "Each scheduled run reports here: the reply it closed with and the files it shared."
                }
              />
              {back ? <div className="mt-4xl">{back}</div> : null}
            </>
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
            <div className="mt-4xl">{back ?? <Pager payload={payload} onPlace={onPlace} />}</div>
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
 *  that fired it — every story states what it is before it states what it did. */
function Story({
  run,
  onPlace,
}: {
  run: RadarRun;
  onPlace: (place: Placement) => void;
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
  return (
    <li className="flex flex-col gap-sm border-b border-edge py-4xl last:border-b-0">
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
          onTitle={at === 0 ? setTitle : undefined}
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

/** The report the run published, read as the story's own body: the document flows inline down the
 *  feed and one longer than the fold opens on the member's word, because a box that scrolls inside
 *  a page that scrolls traps the wheel over the very thing the member came to read. The title line
 *  is dropped where the story already stands under it, and a second document — which titles nothing
 *  above it — keeps its own. The story is told the title before the frame is painted, so no reader
 *  ever catches a report saying its own name twice. */
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
      <Reveal bare>
        <div className="text-body leading-reading">
          <Markdown text={read.title === heading ? read.body : body} />
        </div>
      </Reveal>
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
