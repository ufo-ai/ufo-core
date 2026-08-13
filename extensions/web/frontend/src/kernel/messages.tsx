import { useState, type ReactNode } from "react";

import { Reveal } from "@/components/ui/reveal";
import { speakerName } from "@/lib/audience";
import { cn } from "@/lib/cn";
import { Markdown, StreamingBody } from "@/lib/markdown";
import { subagentConversationHash } from "@/lib/route";
import { formatSize } from "@/lib/size";
import { eventLabel, latestActivity } from "@/lib/turnStream";
import type { ActivityEvent, Bubble, LiveTurn } from "@/lib/chatStore";
import type { ChatFile, SubagentRun } from "@/lib/types";

const PULSE = "size-xs animate-working rounded-full bg-ink motion-reduce:animate-none";

/** One conversation's messages, drawn the one way this portal draws them — the member's words in
 *  a bubble, the agent's as markdown, and under each reply what it did: the subagents it spawned,
 *  the tools it called, the files it shared, the account it asked to connect, what it cost. The
 *  live chat hands it the turn it is streaming and puts a composer under it; a transcript read
 *  back — an agent's conversations, a subagent's runs — hands it none and shows what landed. A
 *  conversation the member cannot reply to still reads exactly like the one they can.
 *
 *  A member message a running turn has not taken up yet states that in its own words — italic and
 *  muted, no line added beneath them: a turn absorbs what arrived at its round boundaries, so the
 *  wait lasts as long as the call it is inside, and the words take their weight back when the
 *  turn says it took them up. Those messages are drawn last, under the reply streaming above them:
 *  the turn answers what it is already inside before it takes up the next thing, so that is the
 *  place the drain will leave them in, and a message drawn anywhere else moves when the fold lands.
 *
 *  A bubble somebody other than the viewer spoke is headed by their name — the read hands it over
 *  only then, so the viewer's own bubbles stay the unlabelled default and the label marks exactly
 *  the words another member said.
 *
 *  `conversationId` is the conversation these messages belong to, and it roots every subagent link
 *  under it: a run is opened through the conversation that spawned it, which is the route by which
 *  a member reading a transcript reaches the run's own record. */
export function MessageLog({
  messages,
  live = null,
  conversationId,
}: {
  messages: Bubble[];
  live?: LiveTurn | null;
  conversationId: string | null;
}) {
  const waiting = messages.findIndex(
    (message) => message.arrival_id !== undefined || message.queued === true,
  );
  const settled = waiting === -1 ? messages : messages.slice(0, waiting);
  const queued = waiting === -1 ? [] : messages.slice(waiting);
  const bubble = (message: Bubble, index: number) =>
    message.role === "error" ? (
      <Meta key={index}>{message.text}</Meta>
    ) : (
      <Speech key={index} mine={message.role === "user"}>
        {message.role === "user" && message.speaker ? (
          <div className="text-label font-medium opacity-(--muted)">
            {speakerName(message.speaker)}
          </div>
        ) : null}
        {message.role === "user" ? null : (
          <Activity
            events={message.events ?? []}
            runs={message.subagents ?? []}
            root={conversationId}
          />
        )}
        {message.role !== "user" ? (
          <Markdown text={message.text} />
        ) : message.arrival_id ? (
          <span className="italic opacity-(--muted)">{message.text}</span>
        ) : (
          message.text
        )}
        {message.files ? <Files files={message.files} /> : null}
        {message.connectUrl ? <ConnectLink url={message.connectUrl} /> : null}
        {message.meta ? <Meta>{message.meta}</Meta> : null}
      </Speech>
    );
  return (
    <>
      {settled.map(bubble)}
      {live ? (
        <Speech mine={false} entering>
          <Activity
            events={live.events}
            runs={live.subagents}
            root={conversationId}
            working={
              live.reconnecting
                ? "Reconnecting…"
                : (live.activity ?? (live.text ? undefined : "Thinking…"))
            }
          />
          <StreamingBody text={live.text} />
          {live.files ? <Files files={live.files} /> : null}
          {live.connectUrl ? <ConnectLink url={live.connectUrl} /> : null}
          {live.meter ? <Meta>{live.meter}</Meta> : null}
          {live.meta ? <Meta>{live.meta}</Meta> : null}
        </Speech>
      ) : null}
      {queued.map((message, index) => bubble(message, settled.length + index))}
    </>
  );
}

function Speech({
  mine,
  entering = false,
  children,
}: {
  mine: boolean;
  entering?: boolean;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "wrap-anywhere [&_a]:text-link",
        mine
          ? "max-w-bubble self-end whitespace-pre-wrap rounded-bubble bg-fill px-lg py-sm"
          : "w-full max-w-bubble self-start text-body leading-reading",
        entering && "animate-appear",
      )}
      data-role={mine ? "me" : "agent"}
    >
      {children}
    </div>
  );
}

export function Meta({ children }: { children: ReactNode }) {
  return (
    <div className="mt-2xs font-mono text-small tabular-nums opacity-(--muted)">
      {children}
    </div>
  );
}

function Working({ children }: { children: ReactNode }) {
  return (
    <div className="mt-2xs flex items-center gap-sm font-mono text-small opacity-(--muted)">
      <span aria-hidden className={PULSE} />
      {children}
    </div>
  );
}

function Activity({
  events,
  runs,
  root,
  working,
}: {
  events: ActivityEvent[];
  runs: SubagentRun[];
  root: string | null;
  working?: string;
}) {
  const [open, setOpen] = useState(false);
  if (!events.length && !runs.length) {
    return working === undefined ? null : <Working>{working}</Working>;
  }
  return (
    <details
      className="mt-2xs font-mono text-small opacity-(--muted)"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="cursor-pointer">
        {working === undefined ? null : (
          <span
            aria-hidden
            className={cn(PULSE, "mr-sm inline-block align-middle")}
          />
        )}
        {working ?? latestActivity(events, runs)}
      </summary>
      {open ? <ActivityTree events={events} runs={runs} root={root} /> : null}
    </details>
  );
}

/** A run's link is rooted at the conversation that spawned it: the one being read for a run under
 *  a reply, and the run's own for the runs it spawned in turn — which is the one generation the
 *  read behind that link authorizes against. */
function ActivityTree({
  events,
  runs,
  root,
}: {
  events: ActivityEvent[];
  runs: SubagentRun[];
  root: string | null;
}) {
  if (!events.length && !runs.length) return null;
  return (
    <ul className="m-0 mt-2xs flex list-none flex-col gap-hair p-0 pl-lg">
      {events.map((event, index) => (
        <li key={index} className="whitespace-pre-wrap">
          {event.kind === "note" ? (
            <Reveal bare>{event.text}</Reveal>
          ) : (
            eventLabel(event, "done")
          )}
        </li>
      ))}
      {runs.map((run) => (
        <li key={run.conversation_id} className="flex flex-col gap-hair">
          <a
            href={subagentConversationHash(
              run.profile,
              run.conversation_id,
              root ?? undefined,
            )}
          >
            Subagent · {run.profile}
          </a>
          <ActivityTree
            events={run.events}
            runs={run.subagents}
            root={run.conversation_id}
          />
          {run.output ? (
            <div className="whitespace-pre-wrap pl-lg">
              <Reveal bare>{run.output}</Reveal>
            </div>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function ConnectLink({ url }: { url: string }) {
  return (
    <a href={url} target="_blank" rel="noopener">
      Connect account
    </a>
  );
}

export function Files({ files }: { files: ChatFile[] }) {
  return (
    <div className="mt-2xs flex flex-col gap-hair">
      {files.map((file) => (
        <div key={file.filename}>
          {file.url ? (
            <a href={file.url}>{file.filename}</a>
          ) : (
            <span>{file.filename}</span>
          )}
          <span className="font-mono text-mono">
            {" "}
            · {formatSize(file.size_bytes)}
          </span>
        </div>
      ))}
    </div>
  );
}
