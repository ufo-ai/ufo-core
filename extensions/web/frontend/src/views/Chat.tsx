import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";

import { CredentialPromptForm } from "@/views/CredentialPrompt";
import { AgentPicker } from "@/kernel/agentpick";
import {
  Questionnaire,
  QuestionnaireActions,
  QuestionnaireChoice,
  QuestionnaireChoiceDescription,
  QuestionnaireChoices,
  QuestionnaireDescription,
  QuestionnaireError,
  QuestionnaireInput,
  QuestionnaireItem,
  QuestionnaireNext,
  QuestionnairePrevious,
  QuestionnaireProgress,
  QuestionnaireSkip,
  QuestionnaireSubmit,
  QuestionnaireTitle,
  type QuestionnaireItemDefinition,
} from "@/components/ui/questionnaire";
import {
  PromptInput,
  PromptInputAttach,
  PromptInputAttachments,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputToolbar,
} from "@/components/ui/prompt-input";
import { SILENT, Toast } from "@/components/ui/toast";
import {
  MessageLog,
  Meta,
  TranscriptPane,
  TranscriptScroll,
  useTakeMeToTheFoot,
} from "@/kernel/messages";
import { COLUMN } from "@/kernel/pane";
import { cn } from "@/lib/cn";
import { chatState, clearChat, updateChat, useChat } from "@/lib/chatStore";
import { clearDraft, installDraftFlush, moveDraft, readDraft, writeDraft } from "@/lib/drafts";
import { useEarlierMessages } from "@/lib/earlier";
import { takePendingAsk, watchPendingAsk } from "@/lib/pendingAsk";
import {
  answerQuestions,
  refreshTranscript,
  resyncChat,
  sendMessage,
  stopTurn,
  type ChatTarget,
} from "@/lib/turnStream";
import type { Agent, ChatQuestion, Member, QuestionEntry } from "@/lib/types";

/** Who a chat is addressed to. A conversation another surface holds names its agent on the rail
 *  row alone, without the boot read listing it, so a chat asks for the facts such a row carries
 *  and never for the whole record. */
export type ChatAgent = { id: string; name: string; model: string };

export type ChatProps = {
  agent: ChatAgent;
  member: Member;
  conversationId: string | null;
  onCreated?: (conversationId: string, title: string) => void;
  onActivity?: (conversationId: string) => void;
  onSettled?: () => void;
  /** Every agent the member may open a conversation with, and the act that switches to one. The
   *  picker they feed stands in the composer of a conversation that has not started: which agent
   *  answers is the last thing settled before the first message, and a conversation is bound to one
   *  agent the moment it opens. */
  agents?: Agent[];
  onPickAgent?: (agentId: string) => void;
  onOpenArtifacts?: () => void;
};

export function Chat({
  agent,
  member,
  conversationId,
  onCreated,
  onActivity,
  onSettled,
  onOpenArtifacts,
  agents,
  onPickAgent,
}: ChatProps) {
  const chatKey = conversationId ?? "new:" + agent.id;
  const draftKey = member.id + "/" + chatKey;
  const state = useChat(chatKey);
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

  const earlier = useEarlierMessages(
    conversationId === null
      ? null
      : "/agents/" + agent.id + "/conversations/" + conversationId + "/transcript",
    state.earlier,
  );
  const messages = state.messages;
  const settled = !state.busy && !state.live;
  /** A conversation nobody has said anything in yet. The transcript is the whole screen once one
   *  exists, so until then the box stands in the middle of the pane, with the agent that would hold
   *  it picked in the box's own toolbar. A message still loading counts as unsaid, or the first
   *  paint would draw the log's layout and the composer would jump on the frame after it. */
  const starting = conversationId === null && settled && !messages?.length;
  const showEmpty = messages !== null && !messages.length && settled;
  const stalled = messages === null ? state.fault : null;
  const credentials = state.handoffs.credentials;
  const held = state.busy || state.messages === null;

  return (
    <TranscriptScroll>
      {starting ? null : (
        <TranscriptPane className="flex-1">
          <MessageLog
            messages={messages ?? []}
            earlier={earlier}
            live={state.live}
            className={cn(COLUMN, "p-2xl")}
            question={(question) => (
              <Question
                target={target}
                question={question}
                held={held}
                onAct={() => composer.current?.focus()}
              />
            )}
            onOpenArtifacts={onOpenArtifacts}
          >
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
                No messages in this conversation yet.
              </div>
            ) : null}
          </MessageLog>
        </TranscriptPane>
      )}
      <Composer
        target={target}
        draftKey={draftKey}
        input={composer}
        starting={starting}
        agent={agent}
        agents={agents}
        onPickAgent={
          onPickAgent
            ? (agentId) => {
                moveDraft(draftKey, member.id + "/new:" + agentId);
                onPickAgent(agentId);
              }
            : undefined
        }
      />
      <Toast
        state={stalled ? SILENT : state.fault ?? SILENT}
        onDone={() => updateChat(chatKey, (current) => ({ ...current, fault: null }))}
      />
    </TranscriptScroll>
  );
}


function Handoff({ children }: { children: ReactNode }) {
  return (
    <div className="flex max-w-bubble flex-col gap-sm self-start rounded-bubble border border-edge px-lg py-md">
      {children}
    </div>
  );
}

/** The most options a turn may offer as choices. Past that the list is taller than the reply it
 *  stands under, and reading it is worse than typing the answer. */
const MAX_ANSWER_OPTIONS = 10;

/** Whether an entry's options are drawn as choices at all. A question that will take a file, and a
 *  list too long to read, are both the message box's job — the form can hold neither, and offering
 *  a control that cannot carry the answer is worse than saying where the answer goes. */
function choosable(entry: QuestionEntry): boolean {
  return Boolean(
    entry.options &&
      entry.options.length <= MAX_ANSWER_OPTIONS &&
      !entry.free_text_only &&
      !entry.allow_attachments,
  );
}

/** Answered by typing into the form: words, and no file to go with them. */
function typed(entry: QuestionEntry): boolean {
  return !entry.allow_attachments && (Boolean(entry.free_text_only) || !(entry.options ?? []).length);
}

/** What a turn asks, standing under the reply that asked it and taken one question at a time. A
 *  turn may ask up to four things; four of them stacked in a transcript is a wall the member has
 *  to read before answering any of it, and the answers to the later ones often depend on the
 *  earlier. A step names one decision, says where it sits in the run, and can be gone back to.
 *
 *  It is a real form, so each answer is a native control carrying a native name and the member's
 *  choice arrives as form data rather than as state a component was holding. An entry the member
 *  has already answered leaves the run and states the words the surface confirmed it admitted —
 *  hidden, it would leave them with no record of what they chose. */
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
  const toTheFoot = useTakeMeToTheFoot();
  const landed = question.answered ?? {};
  const open = asked
    .map((entry, index) => ({ entry, index }))
    .filter(({ index }) => landed[index] === undefined);
  // An entry the form cannot carry — a file to attach, a list too long to read — stands as prose
  // naming where the answer goes. In the form it would be a step demanding a choice it offers no
  // control for, gating the answers the member did give behind an error nothing on screen resolves.
  const steppable = open.filter(({ entry }) => choosable(entry) || typed(entry));
  const prose = open.filter(({ entry }) => !choosable(entry) && !typed(entry));
  // Declared choices and drawn choices are one list: an entry that names options the form does not
  // render leaves the primitive holding a choice with nowhere to be, which it says so on the console.
  const items: QuestionnaireItemDefinition[] = steppable.map(({ entry, index }) => ({
    name: String(index),
    ...(choosable(entry)
      ? { choices: (entry.options ?? []).map((option) => ({ value: option.label })) }
      : {}),
  }));
  // No card around this. Every answer is already a bordered row, and a card holding a stack of
  // cards states a boundary twice — the question belongs in the reply's own column, exactly where
  // the words that asked it are.
  return (
    <div className="mt-lg flex max-w-bubble flex-col gap-lg">
      {question.title ? <div>{question.title}</div> : null}
      {asked.map((entry, index) =>
        landed[index] === undefined ? null : (
          <div key={index} className="flex flex-col gap-xs">
            <div>{entry.header ? entry.header + " — " + entry.question : entry.question}</div>
            <Meta>{landed[index]}</Meta>
          </div>
        ),
      )}
      {prose.map(({ entry, index }) => (
        <div key={index} className="flex flex-col gap-xs">
          <div>{entry.header ? entry.header + " — " + entry.question : entry.question}</div>
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
        </div>
      ))}
      {steppable.length ? (
        <Questionnaire
          items={items}
          onSubmit={(event) => {
            event.preventDefault();
            const answers = new FormData(event.currentTarget);
            onAct();
            toTheFoot();
            void deliver(target, question, asked, steppable, answers);
          }}
        >
          {steppable.length > 1 ? <QuestionnaireProgress /> : null}
          {steppable.map(({ entry, index }) => (
            <QuestionnaireItem key={index} name={String(index)} multiple={entry.multi_select}>
              <QuestionnaireTitle>{entry.header ?? entry.question}</QuestionnaireTitle>
              {entry.header ? (
                <QuestionnaireDescription>{entry.question}</QuestionnaireDescription>
              ) : null}
              <QuestionnaireChoices>
                {choosable(entry)
                  ? (entry.options ?? []).map((option) => (
                      <QuestionnaireChoice key={option.label} value={option.label}>
                        <span className="font-medium">{option.label}</span>
                        {option.description ? (
                          <QuestionnaireChoiceDescription>
                            {option.description}
                          </QuestionnaireChoiceDescription>
                        ) : null}
                      </QuestionnaireChoice>
                    ))
                  : (entry.options ?? []).map((option) => (
                      <Meta key={option.label}>
                        {option.description
                          ? option.label + " — " + option.description
                          : option.label}
                      </Meta>
                    ))}
                {typed(entry) ? (
                  <QuestionnaireInput aria-label={entry.question} placeholder="Your answer" />
                ) : null}
              </QuestionnaireChoices>
              <QuestionnaireError />
            </QuestionnaireItem>
          ))}
          <QuestionnaireActions>
            <QuestionnairePrevious />
            <QuestionnaireSkip />
            <QuestionnaireNext />
            <QuestionnaireSubmit disabled={held} />
          </QuestionnaireActions>
        </Questionnaire>
      ) : null}
    </div>
  );
}

/** Each answer is admitted against the entry it answers, in the order they were asked, because
 *  the turn recorded them as separate questions and reads them back the same way. An entry the
 *  member skipped says nothing rather than saying nothing at length. A single question needs no
 *  restatement; one of several names itself, so the transcript reads as an answer to something. */
async function deliver(
  target: ChatTarget,
  question: ChatQuestion,
  asked: QuestionEntry[],
  open: { entry: QuestionEntry; index: number }[],
  answers: FormData,
): Promise<void> {
  const given = open.flatMap(({ entry, index }) => {
    const values = answers
      .getAll(String(index))
      .map((value) => String(value).trim())
      .filter((value) => value !== "");
    if (!values.length) return [];
    const joined = values.join(", ");
    return [{ index, body: asked.length === 1 ? joined : joined + " · " + entry.question }];
  });
  await answerQuestions(target, question.turn_id, given);
}

/** The message box holds as many lines as the member writes. Enter sends it and Shift+Enter opens
 *  a line, which is the pairing every chat composer ships and the only one a member arrives
 *  already knowing; a composition in flight (an IME candidate) takes its own Enter, so the guard
 *  reads `isComposing` before claiming the key. */
function Composer({
  target,
  draftKey,
  input,
  starting,
  agent,
  agents,
  onPickAgent,
}: {
  target: ChatTarget;
  draftKey: string;
  input: RefObject<HTMLTextAreaElement | null>;
  starting: boolean;
  agent: ChatAgent;
  agents?: Agent[];
  onPickAgent?: (agentId: string) => void;
}) {
  const state = useChat(target.key);
  const toTheFoot = useTakeMeToTheFoot();
  // A panel hands its words to the agent's *new* chat, and only a composer founding one takes
  // them. A conversation the member is already reading belongs to that same agent and its composer
  // is the one on the screen, so a take keyed by the agent alone would say them there — into the
  // conversation they were leaving, over the draft they left in it.
  const founding = target.conversationId === null;
  // The words win over a draft for this open only. Words the member already committed carry
  // `send`, and wait here until the composer can carry them — a new chat has no transcript to add
  // to on its first render.
  const committed = useRef<string | null>(null);
  const [text, setText] = useState(() => {
    const handed = founding ? takePendingAsk(target.agentId) : null;
    if (handed?.send) committed.current = handed.text;
    return handed?.text ?? readDraft(draftKey);
  });
  const [stopping, setStopping] = useState(false);
  // The turn the page is tailing, and so the one a stop can name. A send holds the chat busy before
  // admission answers with a turn id, and a stop of a turn nobody has named yet reaches nothing.
  const running = state.turn;
  // Live during a turn, so a correction reaches the agent mid-reply. The exception is the send that
  // founds a conversation: until it answers there is no conversation for a second message to join,
  // and these words stay in the box rather than opening a conversation of their own.
  const disabled = state.messages === null || (target.conversationId === null && state.busy);

  // An ask handed over while this composer is already mounted — the palette hands one from the
  // chat screen itself — reaches the box here rather than waiting for a mount that never comes.
  useEffect(() => {
    if (!founding) return;
    return watchPendingAsk(() => {
      const handed = takePendingAsk(target.agentId);
      if (!handed) return;
      if (handed.send) committed.current = handed.text;
      setText(handed.text);
    });
  }, [founding, target.agentId]);

  useEffect(() => {
    if (committed.current === null || committed.current !== text || disabled) return;
    committed.current = null;
    send([]);
  }, [disabled, text]);

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
    input.current?.focus();
    toTheFoot();
    void sendMessage(target, body, shown);
    return true;
  }

  return (
    <div
      className={cn(
        COLUMN,
        starting
          ? "flex flex-1 flex-col justify-center overflow-y-auto p-2xl"
          : "px-2xl pt-lg pb-[max(var(--spacing-lg),env(safe-area-inset-bottom))]",
      )}
    >
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
          <div className="flex items-center gap-sm">
            {starting && agents && onPickAgent ? (
              <AgentPicker agentId={agent.id} agents={agents} onPick={onPickAgent} />
            ) : null}
            <PromptInputSubmit
              stops={Boolean(running && state.busy && !text.trim())}
              busy={stopping}
              disabled={disabled}
              onStop={() => {
                if (stopping || !running) return;
                void stop(running.id);
              }}
            />
          </div>
        </PromptInputToolbar>
      </PromptInput>
    </div>
  );
}

