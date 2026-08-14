import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";

import { CredentialPromptForm } from "@/views/CredentialPrompt";
import { Button } from "@/components/ui/button";
import {
  PromptInput,
  PromptInputAttach,
  PromptInputAttachments,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputToolbar,
} from "@/components/ui/prompt-input";
import { SILENT, Toast } from "@/components/ui/toast";
import { MessageLog, Meta } from "@/kernel/messages";
import { COLUMN } from "@/kernel/pane";
import { cn } from "@/lib/cn";
import { chatState, clearChat, updateChat, useChat } from "@/lib/chatStore";
import { clearDraft, installDraftFlush, moveDraft, readDraft, writeDraft } from "@/lib/drafts";
import { formatSize } from "@/lib/size";
import {
  answerQuestion,
  refreshTranscript,
  resyncChat,
  sendMessage,
  stopTurn,
  type ChatTarget,
} from "@/lib/turnStream";
import type { Agent, ChatFile, ChatQuestion, Member, QuestionEntry } from "@/lib/types";

const MAX_ANSWER_BUTTONS = 10;

const PIN_THRESHOLD_PX = 40;

export type ChatProps = {
  agent: Agent;
  member: Member;
  conversationId: string | null;
  onCreated?: (conversationId: string, title: string) => void;
  onActivity?: (conversationId: string) => void;
  onSettled?: () => void;
};

export function Chat({
  agent,
  member,
  conversationId,
  onCreated,
  onActivity,
  onSettled,
}: ChatProps) {
  const chatKey = conversationId ?? "new:" + agent.id;
  const draftKey = member.id + "/" + chatKey;
  const state = useChat(chatKey);
  const log = useRef<HTMLDivElement>(null);
  const atFoot = useRef(true);
  const [pinned, setPinned] = useState(true);
  const pin = (standing: boolean) => {
    atFoot.current = standing;
    setPinned(standing);
  };
  const composer = useRef<HTMLTextAreaElement>(null);
  const wasBusy = useRef(state.busy);
  const target: ChatTarget = {
    key: chatKey,
    agentId: agent.id,
    conversationId,
    onCreated: (created, title) => {
      moveDraft(draftKey, member.id + "/" + created);
      onCreated?.(created, title);
    },
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
    if (wasBusy.current && !state.busy) onSettled?.();
    wasBusy.current = state.busy;
  }, [onSettled, state.busy]);

  useEffect(() => {
    const pane = log.current;
    if (pane === null) return;
    const follow = () => {
      if (atFoot.current) pane.scrollTop = pane.scrollHeight;
    };
    follow();
    const watch = new MutationObserver(follow);
    watch.observe(pane, { characterData: true, childList: true, subtree: true });
    return () => watch.disconnect();
  }, [state]);

  const jumpToBottom = () => {
    const pane = log.current;
    if (!pane) return;
    pin(true);
    pane.scrollTo({
      top: pane.scrollHeight,
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
    });
  };

  const messages = state.messages;
  const showEmpty = messages !== null && !messages.length && !state.busy && !state.live;
  const stalled = messages === null ? state.fault : null;
  const trailing = state.handoffs.files ?? null;
  const credentials = state.handoffs.credentials;
  const held = state.busy || state.messages === null;

  return (
    <>
      <div
        ref={log}
        onScroll={() => {
          const pane = log.current;
          if (!pane) return;
          pin(pane.scrollTop + pane.clientHeight >= pane.scrollHeight - PIN_THRESHOLD_PX);
        }}
        className={cn(
          COLUMN,
          "flex flex-1 flex-col gap-md overscroll-contain overflow-y-auto scrollbar-gutter-stable p-2xl",
        )}
        data-testid="log"
      >
        <MessageLog
          messages={messages ?? []}
          live={state.live}
          conversationId={conversationId}
          question={(question) => (
            <Question
              target={target}
              question={question}
              held={held}
              onAct={() => {
                pin(true);
                composer.current?.focus();
              }}
            />
          )}
        />
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
        {stalled ? (
          <div className="m-auto max-w-empty text-center text-ink-soft">
            <p>{stalled.title}</p>
            {stalled.description ? <p>{stalled.description}</p> : null}
          </div>
        ) : null}
        {showEmpty ? (
          <div className="m-auto max-w-empty text-center text-ink-soft">
            {conversationId === null
              ? "Message " + agent.name + " to start."
              : "No messages in this conversation yet."}
          </div>
        ) : null}
        {!pinned ? (
          <Button
            variant="row"
            size="icon"
            aria-label="Jump to bottom"
            onClick={jumpToBottom}
            className="sticky bottom-0 self-end"
          >
            <svg viewBox="0 0 12 12" aria-hidden className="size-(--size-icon)">
              <path
                d="M3 4.5 6 7.5 9 4.5"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </Button>
        ) : null}
      </div>
      <Composer
        target={target}
        draftKey={draftKey}
        input={composer}
        onSend={() => pin(true)}
      />
      <Toast
        state={stalled ? SILENT : state.fault ?? SILENT}
        onDone={() => updateChat(chatKey, (current) => ({ ...current, fault: null }))}
      />
    </>
  );
}

function Files({ files }: { files: ChatFile[] }) {
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

function Handoff({ children }: { children: ReactNode }) {
  return (
    <div className="flex max-w-bubble flex-col gap-sm self-start rounded-bubble border border-edge px-lg py-md">
      {children}
    </div>
  );
}

function buttonable(entry: QuestionEntry): boolean {
  return Boolean(
    entry.options &&
      entry.options.length <= MAX_ANSWER_BUTTONS &&
      !entry.free_text_only &&
      !entry.allow_attachments,
  );
}

/** What a turn asks, standing under the reply that asked it. Each entry commits on its own act:
 *  the options select and the submit sends, so a multi-select says all of it in one message and a
 *  single choice is still a choice until the member presses. An entry the member has answered
 *  states the words the surface confirmed it admitted — hidden, it would leave the member with no
 *  record of what they chose. */
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
  return (
    <div className="mt-sm">
      <Handoff>
        <div>{question.title}</div>
        {asked.map((entry, index) => (
          <Ask
            key={index}
            target={target}
            question={question}
            entry={entry}
            index={index}
            alone={asked.length === 1}
            held={held}
            onAct={onAct}
          />
        ))}
      </Handoff>
    </div>
  );
}

function Ask({
  target,
  question,
  entry,
  index,
  alone,
  held,
  onAct,
}: {
  target: ChatTarget;
  question: ChatQuestion;
  entry: QuestionEntry;
  index: number;
  alone: boolean;
  held: boolean;
  onAct: () => void;
}) {
  const [chosen, setChosen] = useState<string[]>([]);
  const prompt = entry.header ? entry.header + " — " + entry.question : entry.question;
  const landed = question.answered?.[index];
  if (landed !== undefined) {
    return (
      <div className="flex flex-col gap-xs">
        <div>{prompt}</div>
        <Meta>{landed}</Meta>
      </div>
    );
  }
  const options = entry.options ?? [];
  return (
    <div className="flex flex-col gap-xs">
      <div>{prompt}</div>
      {buttonable(entry) ? (
        <>
          <div className="flex flex-wrap gap-xs">
            {options.map((option) => (
              <Button
                key={option.label}
                variant="option"
                aria-pressed={chosen.includes(option.label)}
                title={option.description}
                onClick={() =>
                  setChosen((current) =>
                    current.includes(option.label)
                      ? current.filter((label) => label !== option.label)
                      : entry.multi_select
                        ? current.concat(option.label)
                        : [option.label],
                  )
                }
              >
                {option.label}
              </Button>
            ))}
          </div>
          <Button
            variant="send"
            className="self-start"
            disabled={held || !chosen.length}
            onClick={() => {
              const joined = chosen.join(", ");
              onAct();
              void answerQuestion(
                target,
                question.turn_id,
                index,
                alone ? joined : joined + " · " + entry.question,
              );
            }}
          >
            Answer
          </Button>
        </>
      ) : (
        <>
          {options.map((option) => (
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
  );
}

/** The message box holds as many lines as the member writes. Enter sends it and Shift+Enter opens
 *  a line, which is the pairing every chat composer ships and the only one a member arrives
 *  already knowing; a composition in flight (an IME candidate) takes its own Enter, so the guard
 *  reads `isComposing` before claiming the key. */
function Composer({
  target,
  draftKey,
  input,
  onSend,
}: {
  target: ChatTarget;
  draftKey: string;
  input: RefObject<HTMLTextAreaElement | null>;
  onSend: () => void;
}) {
  const state = useChat(target.key);
  const [text, setText] = useState(() => readDraft(draftKey));
  const [stopping, setStopping] = useState(false);
  // The turn the page is tailing, and so the one a stop can name. A send holds the chat busy before
  // admission answers with a turn id, and a stop of a turn nobody has named yet reaches nothing.
  const running = state.turn;
  // Live during a turn, so a correction reaches the agent mid-reply. The exception is the send that
  // founds a conversation: until it answers there is no conversation for a second message to join,
  // and these words stay in the box rather than opening a conversation of their own.
  const disabled = state.messages === null || (target.conversationId === null && state.busy);

  useEffect(() => {
    input.current?.focus();
  }, [input]);

  useEffect(() => installDraftFlush(), []);

  async function stop(turnId: string) {
    setStopping(true);
    await stopTurn(target, turnId);
    setStopping(false);
  }

  function send(attached: File[]): boolean {
    const trimmed = text.trim();
    if ((!trimmed && !attached.length) || disabled) return false;
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
    onSend();
    input.current?.focus();
    void sendMessage(target, body, shown);
    return true;
  }

  return (
    <div className={cn(COLUMN, "px-2xl pt-lg pb-[max(var(--spacing-lg),env(safe-area-inset-bottom))]")}>
      <PromptInput onSend={send}>
        <PromptInputAttachments />
        <PromptInputTextarea
          ref={input}
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            writeDraft(draftKey, event.target.value);
          }}
          onKeyDown={(event) => {
            if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
            event.preventDefault();
            event.currentTarget.form?.requestSubmit();
          }}
          autoComplete="off"
          placeholder="Message the agent…"
          aria-label="Message the agent"
        />
        <PromptInputToolbar>
          <PromptInputAttach />
          <PromptInputSubmit
            stops={Boolean(running && state.busy && !text.trim())}
            busy={stopping}
            disabled={disabled}
            onStop={() => {
              if (stopping || !running) return;
              void stop(running.id);
            }}
          />
        </PromptInputToolbar>
      </PromptInput>
    </div>
  );
}
