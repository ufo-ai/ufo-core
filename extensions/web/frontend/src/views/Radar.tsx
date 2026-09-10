import { useCallback, useLayoutEffect, useState } from "react";
import type { MouseEvent as ReactMouseEvent, ReactNode } from "react";

import { buttonVariants } from "@/components/ui/button";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { ARTIFACT_TEXT_BYTES, FileSheet, MediaIcon, useTextArtifact } from "@/kernel/artifact";
import { ObjectDetail, objectAt, slotOf } from "@/kernel/objects";
import type { ObjectAddress } from "@/kernel/objects";
import type { Placement } from "@/kernel/pager";
import { COLUMN, Header, usePageHead } from "@/kernel/pane";
import { Loading, Panel, PanelBlank, PanelEmpty, Section, usePanelRead } from "@/kernel/panel";
import { appended, beside, closed, opened } from "@/kernel/slots";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { slackLink, surfaceWord } from "@/lib/audience";
import { cn } from "@/lib/cn";
import { useAgents } from "@/lib/mainAgent";
import { Markdown } from "@/lib/markdown";
import { Moment } from "@/lib/moments";
import { agentHash, chatHash, sectionHash } from "@/lib/route";
import { formatSize } from "@/lib/size";
import { SurfaceGlyph } from "@/lib/surfaceMark";
import { RADAR_TOUR } from "@/views/radarTour";

const RADAR = "Radar";

const TASK_KIND = "scheduled_task";
const RUN_PREFIX = "run/";

/** Whether a slot in the place names a run of this page's own, which the drawer reads as a story
 *  rather than as a record held at an object address. */
function isRun(id: string): boolean {
  return id.startsWith(RUN_PREFIX) && id !== RUN_PREFIX;
}

/** What the drawer is titled until the report standing in it has named itself. The read may answer
 *  no run at all, and a drawer the member can shut cannot wait on a name that is never coming. */
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

/** The detail route answers a listing row's fields inside `status`, beside the permalink's name. */
type ReportDetail = { name: string; status: Omit<ReportRow, "name"> };

/** The digest rides whole in `entry`: a flat `summary` would collide with the listing row's own. */
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

const TOUR_SLOT = "tour";

function cover(artifacts: RadarArtifact[]): RadarArtifact | null {
  const pictures = artifacts.filter((artifact) => artifact.preview_url !== null);
  return (
    pictures.find((artifact) => artifact.media_type.startsWith("image/")) ?? pictures[0] ?? null
  );
}

const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

/** The calendar day a stamp names, read off the ISO string as it was sent rather than through
 *  `Date`, so no reader's zone shifts a report across midnight. */
function dateKey(iso: string): string {
  return iso.slice(0, 10);
}

/** The day heading: the month written out, because the heading is read as words rather than
 *  scanned as a column of figures. */
function dayName(key: string): string {
  const [year, month, date] = key.split("-");
  return MONTHS[Number(month) - 1] + " " + Number(date) + ", " + year;
}

/** The clock time a report fired, on the twelve-hour clock the heading's date is written for. */
function clock(iso: string): string {
  const hour = Number(iso.slice(11, 13));
  return ((hour % 12) || 12) + ":" + iso.slice(14, 16) + (hour < 12 ? " AM" : " PM");
}

/** One fact of the meta line, read as its mark and its words. The mark names the kind of fact —
 *  whose agent, which service, what file — so the line reads without labels. */
function Fact({ mark, children }: { mark: ReactNode; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-2xs">
      {mark}
      {children}
    </span>
  );
}

function Dot() {
  return (
    <span aria-hidden className="text-ink-faint">
      ·
    </span>
  );
}

function Feed({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const opens = place.opens ?? [];
  const after = place.after ?? "";
  const params = new URLSearchParams({ order_by: "fired_at", order: "desc" });
  if (after) params.set("cursor", after);
  const state = usePanelRead<ReportsPayload>("/objects/report?" + params.toString());
  const now = dateKey(new Date().toISOString());
  return (
    <Panel state={state} loading={() => <FeedSkeleton />}>
      {(payload) => {
        const runs = payload.objects.map(toRun);
        const days = [...new Set(runs.map((run) => dateKey(run.fired_at)))];
        return (
          <>
            <div className="flex flex-col">
              {days.map((key) => {
                const since = key === now;
                const inDay = runs
                  .filter((run) => dateKey(run.fired_at) === key)
                  .sort((one, other) =>
                    since
                      ? other.fired_at.localeCompare(one.fired_at)
                      : one.fired_at.localeCompare(other.fired_at),
                  );
                return (
                  <section key={key} className="flex flex-col first:[&>h2]:pt-0">
                    <h2 className="m-0 py-2xl text-ui font-medium text-ink">
                      {since ? "Today" : dayName(key)}
                    </h2>
                    <ol className="m-0 flex list-none flex-col border-t border-edge p-0">
                      {inDay.map((run) => (
                        <RunRow
                          key={run.turn_id}
                          run={run}
                          since={since}
                          standing={opens.includes(RUN_PREFIX + run.turn_id)}
                        />
                      ))}
                    </ol>
                  </section>
                );
              })}
              {payload.next_cursor ? null : (
                <ol className="m-0 flex list-none flex-col border-t border-edge p-0">
                  <TourRow standing={opens.includes(TOUR_SLOT)} />
                </ol>
              )}
            </div>
            {payload.next_cursor || after ? (
              <div className="flex gap-sm">
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

const SKELETON_ROWS = 4;

/** The feed's placeholder: a day heading over rows drawn where the reports will stand, at the row's
 *  own gutter, column and picture, so nothing on the page moves when the read lands. */
function FeedSkeleton() {
  return (
    <div className="flex flex-col">
      <div className="py-2xl">
        <Skeleton className="h-(--size-notice) w-1/6" />
      </div>
      <ol className="m-0 flex list-none flex-col border-t border-edge p-0">
        {Array.from({ length: SKELETON_ROWS }, (_, index) => (
          <li key={index} className="border-b border-edge">
            <div className="flex items-start gap-2xl px-sm py-2xl">
              <Skeleton className="h-(--size-notice) w-8xl shrink-0" />
              <div className="flex min-w-0 flex-1 flex-col gap-2xs">
                <Skeleton className="h-(--size-notice) w-2/5" />
                <Skeleton className="h-(--size-notice) w-full" />
                <Skeleton className="h-(--size-notice) w-1/3" />
              </div>
              <Skeleton className="h-(--size-digest-picture) w-(--size-thumbnail) shrink-0 rounded-panel" />
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

function RunRow({
  run,
  since,
  standing,
}: {
  run: RadarRun;
  since: boolean;
  standing: boolean;
}) {
  const agent = useAgents().find((entry) => entry.id === run.agent_id);
  const picture = cover(run.artifacts);
  const files = run.artifacts.length;
  return (
    <Row
      href={sectionHash("radar", { opens: [RUN_PREFIX + run.turn_id] })}
      standing={standing}
      when={since ? <Moment at={run.fired_at} /> : clock(run.fired_at)}
      title={run.entry?.title ?? run.task ?? "Scheduled run"}
      body={run.entry?.summary ?? run.text}
      picture={picture?.preview_url ?? null}
    >
      {agent ? (
        <Fact mark={<AgentIcon name={agent.icon} className="size-icon" />}>
          {agentName(agent.name)}
        </Fact>
      ) : null}
      {agent ? <Dot /> : null}
      <Fact mark={<SurfaceGlyph surface={run.surface} />}>{surfaceWord(run.surface)}</Fact>
      {run.task ? <Dot /> : null}
      {run.task ? <span>{run.task}</span> : null}
      {files ? <Dot /> : null}
      {files ? (
        <Fact mark={<MediaIcon mediaType={run.artifacts[0].media_type} />}>
          {files + (files === 1 ? " file" : " files")}
        </Fact>
      ) : null}
    </Row>
  );
}

/** The tour on the rail, shaped as a row so a member reads it the way they read every report above
 *  it. It opens the tour in the drawer beside the feed. */
function TourRow({ standing }: { standing: boolean }) {
  return (
    <Row
      href={sectionHash("radar", { opens: [TOUR_SLOT] })}
      standing={standing}
      when={null}
      title={RADAR_TOUR.title}
      body={RADAR_TOUR.summary}
      picture={null}
    >
      <Fact mark={<AgentIcon name="radar" className="size-icon" />}>{RADAR_TOUR.lead}</Fact>
    </Row>
  );
}

function Row({
  href,
  standing,
  when,
  title,
  body,
  picture,
  children,
}: {
  href: string;
  standing: boolean;
  when: ReactNode;
  title: string;
  body: string | null;
  picture: string | null;
  children: ReactNode;
}) {
  return (
    <li className="border-b border-edge">
      <a
        href={href}
        aria-current={standing || undefined}
        className={cn(
          "flex items-start gap-2xl px-sm py-2xl text-inherit no-underline",
          "hover:bg-fill",
          standing && "bg-fill",
        )}
      >
        <span className="w-8xl shrink-0 pt-hair text-small tabular-nums text-ink-soft">{when}</span>
        <span className="flex min-w-0 flex-1 flex-col gap-2xs">
          <h3 className="m-0 text-ui font-medium text-ink">{title}</h3>
          {body ? <span className="line-clamp-3 max-w-hint text-ui text-ink-soft">{body}</span> : null}
          <span className="flex flex-wrap items-center gap-x-sm gap-y-2xs pt-xs text-small text-ink-soft">
            {children}
          </span>
        </span>
        {picture ? (
          <img
            loading="lazy"
            alt=""
            src={picture}
            className="h-(--size-digest-picture) w-(--size-thumbnail) shrink-0 rounded-panel border border-edge object-cover object-top-left"
          />
        ) : null}
      </a>
    </li>
  );
}

export function Radar({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const [named, setNamed] = useState<{ run: string; name: string } | null>(null);
  const name = useCallback((run: string, name: string) => setNamed({ run, name }), []);
  const opens = place.opens ?? [];
  const band = usePageHead(<Header pinned heading={1} title={RADAR} />);
  return (
    <>
      {band}
      <Section>
        <div className={COLUMN}>
          <Feed place={place} onPlace={onPlace} />
        </div>
      </Section>
      {opens.slice(-1).map((id) =>
        id === TOUR_SLOT ? (
          <TourSheet key={id} opens={opens} onPlace={onPlace} />
        ) : isRun(id) ? (
          <StorySheet
            key={id}
            id={id}
            title={named?.run === id ? named.name : REPORT}
            opens={opens}
            onPlace={onPlace}
            onName={name}
          />
        ) : (
          <RecordSlot
            key={id}
            id={id}
            held={objectAt(id)}
            opens={opens}
            onPlace={onPlace}
          />
        ),
      )}
    </>
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

function TourSheet({
  opens,
  onPlace,
}: {
  opens: string[];
  onPlace: (place: Placement) => void;
}) {
  const out = "text-inherit no-underline hover:underline focus-visible:underline";
  return (
    <Sheet open title={RADAR_TOUR.title} onClose={() => onPlace({ opens: closed(opens, TOUR_SLOT) })}>
      <div className="flex flex-col gap-2xl">
        <div className="text-body leading-reading">
          <Markdown text={RADAR_TOUR.body} />
        </div>
        <p className="m-0 flex flex-wrap gap-x-sm font-mono text-mono text-ink-soft">
          <a href={sectionHash("artifacts")} className={out}>
            Artifacts
          </a>
          <a href={sectionHash("connectors")} className={out}>
            Connectors
          </a>
        </p>
      </div>
    </Sheet>
  );
}

function StorySheet({
  id,
  title,
  opens,
  onPlace,
  onName,
}: {
  id: string;
  title: string;
  opens: string[];
  onPlace: (place: Placement) => void;
  onName: (run: string, name: string) => void;
}) {
  const agents = useAgents();
  const owner = agents.find((agent) => agent.app === "radar") ?? agents[0];
  const pinned = id.slice(RUN_PREFIX.length);
  const [file, setFile] = useState<RadarArtifact | null>(null);
  const detail = usePanelRead<ReportDetail>(
    owner ? "/objects/report/" + pinned + "?agent=" + owner.id : null,
  );
  if (file !== null) return <FileSheet file={file} onClose={() => setFile(null)} />;
  return (
    <Sheet open title={title} onClose={() => onPlace({ opens: closed(opens, id) })}>
      {detail.phase === "failed" && detail.status === 404 ? (
        <PanelBlank body="This report does not exist or is not shared with you." />
      ) : (
        <Panel state={detail} loading={() => <StorySkeleton />}>
          {(payload) => (
            <ol className="m-0 flex list-none flex-col p-0">
              <Story
                run={toRun({
                  ...payload.status,
                  name: payload.name,
                  agent_id: payload.status.agent_id || (owner?.id ?? ""),
                })}
                opens={opens}
                from={id}
                onPlace={onPlace}
                onName={onName}
                onFile={setFile}
              />
            </ol>
          )}
        </Panel>
      )}
    </Sheet>
  );
}

/** The drawer's placeholder: the story's heading, its byline and its meta line over the first lines
 *  of the report, each at the size the story draws it. */
function StorySkeleton() {
  return (
    <div className="flex flex-col gap-sm">
      <Skeleton className="h-(--size-notice) w-3/5 text-title" />
      <Skeleton className="h-(--size-notice) w-2/5" />
      <Skeleton className="h-(--size-notice) w-1/3" />
      <div className="flex flex-col gap-2xs">
        <Skeleton className="h-(--size-notice) w-full" />
        <Skeleton className="h-(--size-notice) w-full" />
        <Skeleton className="h-(--size-notice) w-4/5" />
      </div>
    </div>
  );
}

function isDocument(artifact: RadarArtifact): boolean {
  return (
    artifact.url !== null &&
    (artifact.media_type === "text/markdown" || artifact.filename.endsWith(".md"))
  );
}

function Story({
  run,
  opens,
  from,
  onPlace,
  onName,
  onFile,
}: {
  run: RadarRun;
  opens: string[];
  from?: string;
  onPlace: (place: Placement) => void;
  onName: (run: string, name: string) => void;
  onFile: (artifact: RadarArtifact) => void;
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
      <p className="m-0 flex flex-wrap gap-x-sm font-mono text-mono text-ink-soft">
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
        <ul className="m-0 flex list-none flex-wrap gap-2xl p-0">
          {files.map((artifact) => (
            <li key={artifact.filename}>
              <Shared artifact={artifact} onOpen={() => onFile(artifact)} />
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
        <Loading />
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

function Shared({ artifact, onOpen }: { artifact: RadarArtifact; onOpen: () => void }) {
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
    <button
      type="button"
      onClick={onOpen}
      className="block cursor-pointer border-0 bg-transparent p-0 text-left hover:opacity-muted-soft"
    >
      {card}
    </button>
  );
}
