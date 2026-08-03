import { useEffect, useRef, useState, type FormEvent, type ReactNode, type RefObject } from "react";

import { CredentialPromptForm } from "@/views/CredentialPrompt";
import { Button } from "@/components/ui/button";
import { getJson } from "@/lib/api";
import { cn } from "@/lib/cn";
import { chatState, updateChat, useChat, type Bubble, type ToolEvent } from "@/lib/chatStore";
import { answerQuestion, eventLabel, sendMessage } from "@/lib/turnStream";
import type { Agent, ChatFile, ChatQuestion, QuestionEntry, Transcript } from "@/lib/types";

const MAX_ANSWER_BUTTONS = 10;

export function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  if (bytes >= 1024) return Math.round(bytes / 1024) + " kB";
  return bytes + " B";
}

const PIN_THRESHOLD_PX = 40;

export function Chat({ agent }: { agent: Agent }) {
  const state = useChat(agent.id);
  const log = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const composer = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (chatState(agent.id).messages !== null) return;
    let live = true;
    getJson<Transcript>("/agents/" + agent.id + "/transcript").then((result) => {
      if (!live) return;
      const payload = result.ok ? result.payload : { messages: [] as Bubble[] };
      updateChat(agent.id, (current) =>
        current.messages !== null
          ? current
          : {
              ...current,
              messages: payload.messages as Bubble[],
              handoffs: {
                question: ("question" in payload && payload.question) || null,
                credentials: ("credentials" in payload && payload.credentials) || null,
                files: ("files" in payload && payload.files) || null,
              },
            },
      );
    });
    return () => {
      live = false;
    };
  }, [agent.id]);

  useEffect(() => {
    const pane = log.current;
    if (pane && pinned.current) pane.scrollTop = pane.scrollHeight;
  }, [state]);

  const repin = () => {
    pinned.current = true;
  };

  const messages = state.messages;
  const showEmpty = messages !== null && !messages.length && !state.busy && !state.live;
  const trailing = state.live ? null : state.handoffs.files ?? null;
  const credentials = state.handoffs.credentials;

  return (
    <>
      <div
        ref={log}
        onScroll={() => {
          const pane = log.current;
          if (!pane) return;
          pinned.current =
            pane.scrollTop + pane.clientHeight >= pane.scrollHeight - PIN_THRESHOLD_PX;
        }}
        className="flex flex-1 flex-col gap-md overflow-y-auto p-2xl"
        data-testid="log"
      >
        {(messages ?? []).map((message, index) =>
          message.role === "error" ? (
            <Meta key={index}>{message.text}</Meta>
          ) : (
            <Speech key={index} mine={message.role === "user"}>
              {message.text}
              {message.events ? <ToolFold events={message.events} /> : null}
              {message.files ? <Files files={message.files} /> : null}
              {message.connectUrl ? <ConnectLink url={message.connectUrl} /> : null}
              {message.meta ? <Meta>{message.meta}</Meta> : null}
            </Speech>
          ),
        )}
        {state.live ? (
          <Speech mine={false} entering>
            {state.live.text}
            {state.live.files ? <Files files={state.live.files} /> : null}
            {state.live.connectUrl ? <ConnectLink url={state.live.connectUrl} /> : null}
            {state.live.activity ? <Working>{state.live.activity}</Working> : null}
            {state.live.meter ? <Meta>{state.live.meter}</Meta> : null}
            {state.live.meta ? <Meta>{state.live.meta}</Meta> : null}
          </Speech>
        ) : null}
        {trailing && trailing.length ? <Files files={trailing} /> : null}
        {credentials ? (
          <Handoff>
            <div>{credentials.reason}</div>
            {credentials.prompts.map((prompt) => (
              <CredentialPromptForm
                key={prompt.slot}
                sealed={credentials.sealed}
                prompt={prompt}
                onStored={(slot) =>
                  updateChat(agent.id, (current) => {
                    const request = current.handoffs.credentials;
                    if (!request) return current;
                    return {
                      ...current,
                      handoffs: {
                        ...current.handoffs,
                        credentials: {
                          ...request,
                          prompts: request.prompts.map((entry) =>
                            entry.slot === slot ? { ...entry, stored: true } : entry,
                          ),
                        },
                      },
                    };
                  })
                }
              />
            ))}
          </Handoff>
        ) : null}
        {state.handoffs.question ? (
          <Question
            agent={agent}
            question={state.handoffs.question}
            held={state.busy || state.messages === null}
            onAct={() => {
              repin();
              composer.current?.focus();
            }}
          />
        ) : null}
        {showEmpty ? (
          <div className="m-auto max-w-empty text-center opacity-(--muted-soft)">
            No conversation with {agent.name} yet.
          </div>
        ) : null}
      </div>
      <Composer agent={agent} input={composer} onSend={repin} />
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
        "break-words [&_a]:text-link",
        mine
          ? "max-w-bubble self-end whitespace-pre-wrap rounded-bubble bg-fill px-lg py-sm"
          : "w-full max-w-bubble self-start whitespace-pre-wrap text-body leading-reading",
        entering && "animate-appear",
      )}
      data-role={mine ? "me" : "agent"}
    >
      {children}
    </div>
  );
}

function Meta({ children }: { children: ReactNode }) {
  return (
    <div className="mt-2xs font-mono text-small tabular-nums opacity-(--muted)">{children}</div>
  );
}

function Working({ children }: { children: ReactNode }) {
  return (
    <div className="mt-2xs flex items-center gap-sm font-mono text-small opacity-(--muted)">
      <span aria-hidden className="h-xs w-xs animate-working rounded-full bg-ink motion-reduce:animate-none" />
      {children}
    </div>
  );
}

function ToolFold({ events }: { events: ToolEvent[] }) {
  return (
    <details className="mt-2xs font-mono text-small opacity-(--muted)">
      <summary className="cursor-pointer">
        {events.length === 1 ? "1 tool call" : events.length + " tool calls"}
      </summary>
      <ul className="m-0 mt-2xs flex list-none flex-col gap-hair p-0 pl-lg">
        {events.map((event, index) => (
          <li key={index}>{eventLabel(event, "done")}</li>
        ))}
      </ul>
    </details>
  );
}

function Handoff({ children }: { children: ReactNode }) {
  return (
    <div className="flex max-w-bubble flex-col gap-sm self-start rounded-bubble border border-edge-control px-lg py-md">
      {children}
    </div>
  );
}

function ConnectLink({ url }: { url: string }) {
  return (
    <a href={url} target="_blank" rel="noopener">
      Connect account
    </a>
  );
}

function Files({ files }: { files: ChatFile[] }) {
  return (
    <div className="mt-2xs flex flex-col gap-hair">
      {files.map((file) => (
        <div key={file.filename}>
          {file.url ? <a href={file.url}>{file.filename}</a> : <span>{file.filename}</span>}
          <span className="font-mono text-mono"> · {formatSize(file.size_bytes)}</span>
        </div>
      ))}
    </div>
  );
}

function buttonable(entry: QuestionEntry): boolean {
  return Boolean(
    entry.options &&
      entry.options.length <= MAX_ANSWER_BUTTONS &&
      !entry.multi_select &&
      !entry.free_text_only &&
      !entry.allow_attachments,
  );
}

function Question({
  agent,
  question,
  held,
  onAct,
}: {
  agent: Agent;
  question: ChatQuestion;
  held: boolean;
  onAct: () => void;
}) {
  const asked: QuestionEntry[] = question.questions ?? [];
  const answered = question.answered ?? [];
  return (
    <Handoff>
      <div>{question.title}</div>
      {asked.map((entry, index) =>
        answered.includes(index) ? null : (
          <div key={index} className="flex flex-col gap-xs">
            <div>{entry.header ? entry.header + " — " + entry.question : entry.question}</div>
            {buttonable(entry) ? (
              <div className="flex flex-wrap gap-xs">
                {(entry.options ?? []).map((option) => (
                  <Button
                    key={option.label}
                    variant="option"
                    title={option.description}
                    onClick={() => {
                      if (held) return;
                      onAct();
                      void answerQuestion(
                        agent.id,
                        question.turn_id,
                        index,
                        asked.length === 1
                          ? option.label
                          : option.label + " · " + entry.question,
                      );
                    }}
                  >
                    {option.label}
                  </Button>
                ))}
              </div>
            ) : (
              <>
                {(entry.options ?? []).map((option) => (
                  <Meta key={option.label}>
                    {option.description ? option.label + " — " + option.description : option.label}
                  </Meta>
                ))}
                <Meta>
                  {entry.multi_select
                    ? "Select all that apply — answer in the message box below."
                    : "Answer in the message box below."}
                </Meta>
              </>
            )}
          </div>
        ),
      )}
    </Handoff>
  );
}

function Composer({
  agent,
  input,
  onSend,
}: {
  agent: Agent;
  input: RefObject<HTMLInputElement | null>;
  onSend: () => void;
}) {
  const state = useChat(agent.id);
  const [text, setText] = useState("");
  const files = useRef<HTMLInputElement>(null);
  const disabled = state.busy || state.messages === null;

  useEffect(() => {
    input.current?.focus();
  }, [input]);

  function submit(event: FormEvent) {
    event.preventDefault();
    const attached = Array.from(files.current?.files ?? []);
    const trimmed = text.trim();
    if ((!trimmed && !attached.length) || disabled) return;
    setText("");
    const shown = trimmed || attached.map((file) => file.name).join(", ");
    let body: string | FormData = trimmed;
    if (attached.length) {
      const form = new FormData();
      form.set("message", trimmed);
      for (const file of attached) form.append("file", file);
      body = form;
    }
    if (files.current) files.current.value = "";
    onSend();
    input.current?.focus();
    sendMessage(agent.id, body, shown);
  }

  return (
    <form onSubmit={submit} className="flex gap-sm border-t border-edge px-2xl py-lg">
      <label
        title="Attach files"
        className="flex cursor-pointer items-center rounded-panel border border-edge-control px-lg font-strong"
      >
        <input ref={files} type="file" multiple hidden />+
      </label>
      <input
        ref={input}
        value={text}
        onChange={(event) => setText(event.target.value)}
        autoComplete="off"
        placeholder="Message the agent…"
        aria-label="Message the agent"
        className="flex-1 rounded-panel border border-edge-control bg-field px-lg py-md text-field-ink"
      />
      <Button type="submit" variant="send" disabled={disabled}>
        Send
      </Button>
    </form>
  );
}
