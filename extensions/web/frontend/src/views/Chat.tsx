import { useEffect, useRef, useState, type FormEvent, type ReactNode, type RefObject } from "react";

import { CredentialPromptForm } from "@/views/CredentialPrompt";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { Markdown, StreamingBody } from "@/lib/markdown";
import { chatState, clearChat, updateChat, useChat, type ToolEvent } from "@/lib/chatStore";
import { clearDraft, installDraftFlush, readDraft, writeDraft } from "@/lib/drafts";
import {
  answerQuestion,
  eventLabel,
  refreshTranscript,
  resyncChat,
  sendMessage,
  type ChatTarget,
} from "@/lib/turnStream";
import type { Agent, ChatFile, ChatQuestion, Member, QuestionEntry } from "@/lib/types";

const MAX_ANSWER_BUTTONS = 10;

export function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  if (bytes >= 1024) return Math.round(bytes / 1024) + " kB";
  return bytes + " B";
}

const PIN_THRESHOLD_PX = 40;

export type ChatProps = {
  agent: Agent;
  member: Member;
  conversationId: string | null;
  onCreated?: (conversationId: string, title: string) => void;
  onActivity?: (conversationId: string) => void;
};

export function Chat({ agent, member, conversationId, onCreated, onActivity }: ChatProps) {
  const chatKey = conversationId ?? "new:" + agent.id;
  const state = useChat(chatKey);
  const log = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const composer = useRef<HTMLInputElement>(null);
  const target: ChatTarget = {
    key: chatKey,
    agentId: agent.id,
    conversationId,
    onCreated,
    onAccepted: onActivity,
  };
  const live = useRef(target);
  live.current = target;

  useEffect(() => {
    if (conversationId !== null) {
      void refreshTranscript(live.current, true);
      return;
    }
    const held = chatState(chatKey);
    if (held.messages !== null && !held.busy && held.messages.at(-1)?.role === "error") {
      clearChat(chatKey);
    }
    updateChat(chatKey, (current) =>
      current.messages !== null ? current : { ...current, messages: [] },
    );
  }, [chatKey, conversationId]);

  useEffect(() => {
    const sync = () => {
      if (document.visibilityState === "visible") resyncChat(live.current);
    };
    window.addEventListener("focus", sync);
    document.addEventListener("visibilitychange", sync);
    return () => {
      window.removeEventListener("focus", sync);
      document.removeEventListener("visibilitychange", sync);
    };
  }, [chatKey]);

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
              {message.role === "user" ? message.text : <Markdown text={message.text} />}
              {message.events ? <ToolFold events={message.events} /> : null}
              {message.files ? <Files files={message.files} /> : null}
              {message.connectUrl ? <ConnectLink url={message.connectUrl} /> : null}
              {message.meta ? <Meta>{message.meta}</Meta> : null}
            </Speech>
          ),
        )}
        {state.live ? (
          <Speech mine={false} entering>
            <StreamingBody text={state.live.text} />
            {state.live.files ? <Files files={state.live.files} /> : null}
            {state.live.connectUrl ? <ConnectLink url={state.live.connectUrl} /> : null}
            {state.live.reconnecting ? (
              <Working>Reconnecting…</Working>
            ) : state.live.activity ? (
              <Working>{state.live.activity}</Working>
            ) : null}
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
                  updateChat(chatKey, (current) => {
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
            target={target}
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
            {conversationId === null
              ? "Message " + agent.name + " to start."
              : "No messages in this conversation yet."}
          </div>
        ) : null}
      </div>
      <Composer
        target={target}
        draftKey={member.id + "/" + chatKey}
        input={composer}
        onSend={repin}
      />
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
          : "w-full max-w-bubble self-start text-body leading-reading",
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
  target,
  question,
  held,
  onAct,
}: {
  target: ChatTarget;
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
                        target,
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
  target,
  draftKey,
  input,
  onSend,
}: {
  target: ChatTarget;
  draftKey: string;
  input: RefObject<HTMLInputElement | null>;
  onSend: () => void;
}) {
  const state = useChat(target.key);
  const [text, setText] = useState(() => readDraft(draftKey));
  const files = useRef<HTMLInputElement>(null);
  const disabled = state.busy || state.messages === null;

  useEffect(() => {
    input.current?.focus();
  }, [input]);

  useEffect(() => installDraftFlush(), []);

  function submit(event: FormEvent) {
    event.preventDefault();
    const attached = Array.from(files.current?.files ?? []);
    const trimmed = text.trim();
    if ((!trimmed && !attached.length) || disabled) return;
    setText("");
    clearDraft(draftKey);
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
    void sendMessage(target, body, shown);
  }

  return (
    <form onSubmit={submit} className="flex gap-sm border-t border-edge px-2xl pt-lg pb-[max(var(--spacing-lg),env(safe-area-inset-bottom))]">
      <label
        title="Attach files"
        className="flex cursor-pointer items-center rounded-panel border border-edge-control px-lg font-strong"
      >
        <input ref={files} type="file" multiple hidden />+
      </label>
      <input
        ref={input}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          writeDraft(draftKey, event.target.value);
        }}
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
