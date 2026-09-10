import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";

import { IconCheck, IconChevronRight, IconMessage, IconPlug } from "@tabler/icons-react";

import { CredentialPromptForm, MCP_SERVERS_SLOT } from "@/views/CredentialPrompt";
import {
  Questionnaire,
  QuestionnaireActions,
  QuestionnaireChoice,
  QuestionnaireChoiceDescription,
  QuestionnaireChoices,
  QuestionnaireError,
  QuestionnaireInput,
  QuestionnaireItem,
  QuestionnaireOnward,
  QuestionnaireSkip,
  QuestionnaireStepper,
  QuestionnaireSubmit,
  QuestionnaireTitle,
  KEY,
  ROW,
  type QuestionnaireItemDefinition,
} from "@/components/ui/questionnaire";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  PromptInput,
  PromptInputAttach,
  PromptInputAttachments,
  PromptInputEyebrow,
  PromptInputModel,
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
import { PressRow, PRESS_ROW, PRESS_ROW_CHEVRON } from "@/components/ui/pressrow";
import { takeFocus } from "@/kernel/focus";
import { COLUMN } from "@/kernel/pane";
import { Empty, usePanelRead } from "@/kernel/panel";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { BrandMark } from "@/lib/brandMark";
import { cn } from "@/lib/cn";
import { chatState, clearChat, updateChat, useChat } from "@/lib/chatStore";
import {
  clearDraft,
  flushDrafts,
  installDraftFlush,
  moveDraft,
  readDraft,
  writeDraft,
} from "@/lib/drafts";
import { useEarlierMessages } from "@/lib/earlier";
import { CHAT_SURFACE } from "@/lib/mainAgent";
import { AUTO_MODEL } from "@/lib/models";
import { setPendingAsk, takePendingAsk, watchPendingAsk } from "@/lib/pendingAsk";
import { newChatHash, sectionHash } from "@/lib/route";
import { navigate, useRoute } from "@/lib/router";
import { uploadAttachment, type UploadRef } from "@/lib/api";
import {
  answerQuestions,
  refreshTranscript,
  resyncChat,
  sendMessage,
  stopTurn,
  type ChatTarget,
} from "@/lib/turnStream";
import type { ChatQuestion, Member, QuestionEntry, QuestionOption } from "@/lib/types";

export type ChatAgent = {
  id: string;
  name: string;
  model: string;
  icon?: string;
  app?: string | null;
};

export type ChatProps = {
  agent: ChatAgent;
  member: Member;
  conversationId: string | null;
  foundingKey?: string;
  focusComposer?: boolean;
  unsaid?: ReactNode;
  onCreated?: (conversationId: string, title: string) => void;
  onActivity?: (conversationId: string) => void;
  onSettled?: () => void;
};

export function Chat({
  agent,
  member,
  conversationId,
  foundingKey,
  focusComposer = false,
  unsaid,
  onCreated,
  onActivity,
  onSettled,
}: ChatProps) {
  const chatKey = conversationId ?? foundingKey ?? "new:" + agent.id;
  const draftKey = member.id + "/" + chatKey;
  const state = useChat(chatKey);
  const route = useRoute();
  const composer = useRef<HTMLTextAreaElement>(null);
  const wasBusy = useRef(state.busy);
  const target: ChatTarget = {
    key: chatKey,
    agentId: agent.id,
    agentModel: agent.model,
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
    if (focusComposer) takeFocus(composer.current);
  }, [focusComposer]);

  useEffect(() => {
    if (conversationId !== null) {
      void refreshTranscript(live.current, true);
      return;
    }
    readyToFound(chatKey);
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
    state.earlierCursor,
  );
  const messages = state.messages;
  const settled = !state.busy && !state.live;
  const starting = conversationId === null && settled && !messages?.length;
  const bare = starting && unsaid === undefined;
  const showEmpty = messages !== null && !messages.length && settled;
  const [animate, setAnimate] = useState(false);
  const settling = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(settling.current), []);
  const animateTheSend = () => {
    setAnimate(true);
    window.clearTimeout(settling.current);
    settling.current = window.setTimeout(() => setAnimate(false), SEND_SCROLL_MS);
  };
  const stalled = messages === null ? state.fault : null;
  const credentials = state.handoffs.credentials;
  const held = state.busy || state.messages === null;

  return (
    <TranscriptScroll>
      {starting && !bare ? unsaid : null}
      {starting ? null : (
        <TranscriptPane className="flex-1" animate={animate}>
          <MessageLog
            messages={messages ?? []}
            earlier={earlier}
            live={state.live}
            report={
              route.kind === "chat" && route.conversationId === conversationId
                ? (route.report ?? null)
                : null
            }
            className={cn(COLUMN, "p-2xl")}
            question={(question) => (
              <Question
                target={target}
                question={question}
                held={held}
                onAct={() => composer.current?.focus()}
              />
            )}
          >
            {credentials ? (
              <Handoff>
                <div>
                  {credentials.prompts.length === 1 &&
                  credentials.prompts[0].slot === MCP_SERVERS_SLOT
                    ? "Add or update one MCP server. Saved servers stay in place."
                    : credentials.reason}
                </div>
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
              <Empty>
                <p>{stalled.title}</p>
                {stalled.description ? <p>{stalled.description}</p> : null}
              </Empty>
            ) : null}
            {showEmpty ? <Empty>No messages in this conversation yet.</Empty> : null}
          </MessageLog>
        </TranscriptPane>
      )}
      <Composer
        agent={agent}
        target={target}
        draftKey={draftKey}
        input={composer}
        starting={bare}
        onSent={animateTheSend}
        placeholder={conversationId !== null ? FOLLOW_UP_PLACEHOLDER : NEW_CHAT_PLACEHOLDER}
      />
      <Toast
        state={stalled ? SILENT : state.fault ?? SILENT}
        onDone={() => updateChat(chatKey, (current) => ({ ...current, fault: null }))}
      />
    </TranscriptScroll>
  );
}

function readyToFound(chatKey: string): void {
  const held = chatState(chatKey);
  if (held.messages !== null && !held.busy && held.messages.at(-1)?.role === "error") {
    clearChat(chatKey);
  }
  updateChat(chatKey, (current) =>
    current.messages !== null ? current : { ...current, messages: [] },
  );
}

/** A screen standing over the box that founds a conversation: whatever it draws scrolls over the
 *  box at the bottom. It is the chat screen's own shape — the same `TranscriptScroll` and the same
 *  box with the same toolbar — with a caller's own content where the transcript would be.
 *
 *  The box is keyed on the agent's new chat, the key the start screen's box already uses, so words
 *  a member leaves in one of them are the words the other opens holding. The send founds the
 *  conversation and the draft moves to it, exactly as it does on the start screen — this draws the
 *  box in a second place, never a second box. */
export function FoundingChat({
  agent,
  member,
  onCreated,
  children,
}: {
  agent: ChatAgent;
  member: Member;
  onCreated: (conversationId: string, title: string) => void;
  children: ReactNode;
}) {
  const composer = useRef<HTMLTextAreaElement>(null);
  const chatKey = "new:" + agent.id;
  const draftKey = member.id + "/" + chatKey;
  useEffect(() => readyToFound(chatKey), [chatKey]);
  const target: ChatTarget = {
    key: chatKey,
    agentId: agent.id,
    agentModel: agent.model,
    conversationId: null,
    onCreated: (created, title) => {
      moveDraft(draftKey, member.id + "/" + created);
      onCreated(created, title);
    },
  };
  return (
    <TranscriptScroll>
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      <Composer
        agent={agent}
        target={target}
        draftKey={draftKey}
        input={composer}
        starting={false}
        placeholder={NEW_CHAT_PLACEHOLDER}
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


const MAX_ANSWER_OPTIONS = 10;

const MIN_ANSWER_OPTIONS = 2;

function choosable(entry: QuestionEntry): boolean {
  return Boolean(
    entry.options &&
      entry.options.length >= MIN_ANSWER_OPTIONS &&
      entry.options.length <= MAX_ANSWER_OPTIONS &&
      !entry.free_text_only &&
      !entry.allow_attachments,
  );
}

function typed(entry: QuestionEntry): boolean {
  return (
    !entry.allow_attachments &&
    (Boolean(entry.free_text_only) || (entry.options ?? []).length < MIN_ANSWER_OPTIONS)
  );
}

const KEYS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

type SettledRow = { entry: QuestionEntry; answer: string };

function answerKey(entry: QuestionEntry, words: string): string {
  const options = choosable(entry) ? (entry.options ?? []) : [];
  const at = options.findIndex((option) => option.label === words);
  return KEYS[at === -1 ? options.length : at];
}

const ANSWERED_MS = 320;

function opened(entry: QuestionEntry): string | undefined {
  const options = entry.options ?? [];
  if (entry.chosen) return entry.chosen;
  const lone = options.length === 1 && !entry.free_text_only && !entry.allow_attachments;
  return lone ? options[0].label : undefined;
}

function already(entry: QuestionEntry): boolean {
  return !entry.multi_select && opened(entry) !== undefined;
}

function written(entry: QuestionEntry): string | undefined {
  const answer = opened(entry);
  if (choosable(entry) && (entry.options ?? []).some((option) => option.label === answer)) {
    return undefined;
  }
  return answer;
}

function suggested(entry: QuestionEntry): QuestionOption[] {
  const options = entry.options ?? [];
  return options.filter((option) => option.label !== written(entry));
}

function Settled({ rows, restated }: { rows: SettledRow[]; restated: boolean }) {
  return (
    <div className="flex flex-col gap-md rounded-panel bg-fill p-lg">
      {rows.map(({ entry, answer }, index) => {
        const words = restated ? answer.slice(0, -(entry.question.length + 3)) : answer;
        return (
          <div key={index} className="flex flex-col gap-sm">
            <div className="text-ui leading-chrome font-medium text-pretty">{entry.question}</div>
            <div className={cn(ROW, "bg-surface text-ink-soft")}>
              <span aria-hidden className={KEY}>
                {answerKey(entry, words)}
              </span>
              <span className="min-w-0 flex-1 truncate">{words}</span>
              <IconCheck aria-hidden className="size-icon shrink-0" />
            </div>
          </div>
        );
      })}
    </div>
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
  const toTheFoot = useTakeMeToTheFoot();
  const landed = question.answered ?? {};
  const open = question.closed
    ? []
    : asked
        .map((entry, index) => ({ entry, index }))
        .filter(({ index }) => landed[index] === undefined);
  const settled = asked
    .map((entry, index) => ({ entry, answer: landed[index] }))
    .filter((row): row is SettledRow => row.answer !== undefined);
  const steppable = open.filter(({ entry }) => choosable(entry) || typed(entry));
  const prose = open.filter(({ entry }) => !choosable(entry) && !typed(entry));
  // Declared choices and drawn choices are one list: an entry naming options the form does not render
  // leaves the primitive holding a choice with nowhere to be, which it says on the console.
  const items: QuestionnaireItemDefinition[] = steppable.map(({ entry, index }) => ({
    name: String(index),
    ...(choosable(entry)
      ? { choices: (entry.options ?? []).map((option) => ({ value: option.label })) }
      : {}),
  }));
  const names = items.map((item) => item.name);
  const onward = (from: number): string | undefined => {
    const next = steppable.findIndex(({ entry }, index) => index > from && !already(entry));
    return next === -1 ? undefined : names[next];
  };
  const [at, setAt] = useState(onward(-1) ?? names[0]);
  const moving = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => clearTimeout(moving.current ?? undefined), []);
  return (
    <div className="mt-lg flex max-w-bubble flex-col gap-lg rounded-panel border border-edge p-xl">
      {question.title || question.icon ? (
        <div className="flex items-center gap-lg">
          <div className="min-w-0 flex-1 text-label font-medium text-ink-soft">{question.title}</div>
          {question.icon ? (
            <Avatar>
              <AvatarFallback>
                <AgentIcon name={question.icon} />
              </AvatarFallback>
            </Avatar>
          ) : null}
        </div>
      ) : null}
      {settled.length ? <Settled rows={settled} restated={asked.length !== 1} /> : null}
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
          item={at}
          onItemChange={(name) => {
            clearTimeout(moving.current ?? undefined);
            setAt(name);
          }}
          onSubmit={(event) => {
            event.preventDefault();
            const answers = new FormData(event.currentTarget);
            onAct();
            toTheFoot();
            void deliver(target, question, asked, steppable, answers);
          }}
        >
          {steppable.map(({ entry, index }) => (
            <QuestionnaireItem key={index} name={String(index)} multiple={entry.multi_select}>
              <QuestionnaireTitle>{entry.question}</QuestionnaireTitle>
              <QuestionnaireChoices>
                {choosable(entry)
                  ? (entry.options ?? []).map((option) => (
                      <QuestionnaireChoice
                        key={option.label}
                        value={option.label}
                        defaultChecked={option.label === entry.chosen}
                        // Arrowing through a radio group fires a click on every option the arrows pass — detail 0, no pointer
                        // under it — so a run that moved on those would carry a keyboard member off the question mid-read.
                        onClick={(event) => {
                          if (entry.multi_select || event.detail === 0) return;
                          const next = onward(names.indexOf(String(index)));
                          if (next === undefined) return;
                          clearTimeout(moving.current ?? undefined);
                          moving.current = setTimeout(() => setAt(next), ANSWERED_MS);
                        }}
                      >
                        <span>{option.label}</span>
                        {option.description ? (
                          <QuestionnaireChoiceDescription>
                            {option.description}
                          </QuestionnaireChoiceDescription>
                        ) : null}
                      </QuestionnaireChoice>
                    ))
                  : suggested(entry).map((option) => (
                      <Meta key={option.label}>
                        {option.description
                          ? option.label + " — " + option.description
                          : option.label}
                      </Meta>
                    ))}
                {entry.multi_select && choosable(entry) ? null : (
                  <QuestionnaireInput
                    aria-label={entry.question}
                    placeholder="Your answer"
                    shortcut={KEYS[choosable(entry) ? (entry.options ?? []).length : 0]}
                    defaultValue={written(entry)}
                  />
                )}
              </QuestionnaireChoices>
              <QuestionnaireError />
            </QuestionnaireItem>
          ))}
          <QuestionnaireActions>
            {steppable.length > 1 ? <QuestionnaireStepper /> : null}
            <QuestionnaireSkip />
            <QuestionnaireOnward />
            <QuestionnaireSubmit disabled={held} />
          </QuestionnaireActions>
        </Questionnaire>
      ) : null}
    </div>
  );
}

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

/** How long the transcript stays animated after a send: long enough for the anchor scroll the send
 *  causes to finish, short enough that a reply landing just after it still places instantly. */
const SEND_SCROLL_MS = 700;

const COMPOSER_LABEL = "Ask UFO";
const NEW_CHAT_PLACEHOLDER = "Start new chat…";
const FOLLOW_UP_PLACEHOLDER = "Ask a follow-up…";

/** A composition in flight — an IME candidate — takes its own Enter, so the guard reads `isComposing`
 *  before claiming the key. */
function Composer({
  agent,
  target,
  draftKey,
  input,
  starting,
  placeholder,
  onSent,
}: {
  agent: ChatAgent;
  target: ChatTarget;
  draftKey: string;
  input: RefObject<HTMLTextAreaElement | null>;
  starting: boolean;
  placeholder: string;
  onSent?: () => void;
}) {
  const state = useChat(target.key);
  const founding = target.conversationId === null;
  const committed = useRef<string | null>(null);
  const [text, setText] = useState(() => {
    const handed = founding ? takePendingAsk(target.agentId, target.key) : null;
    if (handed?.send) committed.current = handed.text;
    return handed?.text ?? readDraft(draftKey);
  });
  const [stopping, setStopping] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);
  const model = picked ?? agent.model;
  /** A conversation's agent reaches this box as a `ConversationAgent`, which carries no `app`, so the
   *  chat surface arrives named but unclassed. */
  const addressed = [agent.app, agent.name].includes(CHAT_SURFACE) ? null : agentName(agent.name);
  const showsEyebrow = addressed !== null && !dismissed;
  const running = state.turn;
  const disabled = state.messages === null || (target.conversationId === null && state.busy);

  /** The pick is this thread's, so it stands while the agent's own model moves under it — and it
   *  goes where the pane keeps this box across an agent change. */
  const held = useRef(agent.id);
  useEffect(() => {
    if (held.current === agent.id) return;
    held.current = agent.id;
    setPicked(null);
  }, [agent.id]);

  const drafted = useRef(draftKey);
  useEffect(() => {
    if (drafted.current === draftKey) return;
    drafted.current = draftKey;
    flushDrafts();
    setText(readDraft(draftKey));
  }, [draftKey]);

  useEffect(() => {
    if (!founding) return;
    const take = () => {
      const handed = takePendingAsk(target.agentId, target.key);
      if (!handed) return;
      if (handed.send) committed.current = handed.text;
      setText(handed.text);
    };
    take();
    return watchPendingAsk(take);
  }, [founding, target.agentId]);

  useEffect(() => {
    if (committed.current === null || committed.current !== text || disabled) return;
    committed.current = null;
    send([]);
  }, [disabled, text]);

  useEffect(() => installDraftFlush(), []);

  async function stop(turnId: string) {
    setStopping(true);
    await stopTurn(target, turnId);
    setStopping(false);
  }

  /** A turn pins a concrete id, so the Auto row carries no pin and leaves the agent's own model
   *  to resolve per turn. */
  function pick(chosen: string) {
    setPicked(chosen === AUTO_MODEL || chosen === agent.model ? null : chosen);
  }

  async function send(attached: File[]): Promise<boolean> {
    const trimmed = text.trim();
    if ((!trimmed && !attached.length) || disabled) return false;
    setText("");
    clearDraft(draftKey);
    const uploaded: UploadRef[] = [];
    const inline: File[] = [];
    for (const file of attached) {
      const ref = await uploadAttachment(file);
      if (ref === null) inline.push(file);
      else uploaded.push(ref);
    }
    let body: string | FormData = trimmed;
    if (uploaded.length || inline.length) {
      const form = new FormData();
      form.set("message", trimmed);
      for (const ref of uploaded) {
        form.append("uploaded_key", ref.key);
        form.append("uploaded_sig", ref.sig);
      }
      for (const file of inline) form.append("file", file);
      body = form;
    }
    input.current?.focus();
    onSent?.();
    void sendMessage(target, body, trimmed, attached, picked);
    return true;
  }

  const box = (
    <PromptInput onSend={send}>
      {showsEyebrow ? (
        <PromptInputEyebrow
          glyph={
            agent.icon ? (
              <AgentIcon name={agent.icon} className="size-(--size-glyph) shrink-0" />
            ) : null
          }
          label={addressed}
          onDismiss={() => setDismissed(true)}
        />
      ) : null}
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
        placeholder={placeholder}
        aria-label={COMPOSER_LABEL}
      />
      <PromptInputToolbar>
        <PromptInputAttach />
        <div className="flex items-center gap-sm">
          <PromptInputModel model={model} onPick={pick} />
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
  );
  return (
    <div
      data-testid={starting ? "start" : undefined}
      className={starting ? "flex flex-1 flex-col overflow-y-auto" : undefined}
      onMouseDown={
        starting
          ? (event) => {
              if (!event.currentTarget.contains(event.target as Node)) return;
              if ((event.target as Element).closest("[data-field-card]")) return;
              event.preventDefault();
              input.current?.focus();
            }
          : undefined
      }
    >
      <div
        className={cn(
          COLUMN,
          "px-2xl pt-lg pb-[max(var(--spacing-lg),env(safe-area-inset-bottom))]",
          starting && "my-auto",
        )}
      >
        {box}
        {starting ? <Starters agentId={target.agentId} /> : null}
      </div>
    </div>
  );
}

type StarterRow = {
  kind: "app" | "check_in" | "unlock";
  mark: string | null;
  line: string;
  ask: string;
  agent_id?: string | null;
  providers?: MissingTile[];
};
type MissingTile = { name: string; label: string };
type UnlockRow = {
  line: string;
  ask: string;
  agent_id?: string | null;
  providers: MissingTile[];
};
type StartersPayload = { starters: StarterRow[]; unlock: UnlockRow | null };

const STARTERS_READ = "/workspace/starters";
const STARTERS_EVERY_MS = 300_000;

const STARTERS: { mark: string; line: string; ask: string }[] = [
  {
    mark: "wedjat",
    line: "Track the competitors you name, with a source for every claim.",
    ask: "I want an application that tracks the competitors I name and writes up what changed, with a source for each claim.",
  },
  {
    mark: "nephele",
    line: "Research a market, company, or person on request.",
    ask: "I want an application that researches a market, company, or person on request and cites every claim.",
  },
  {
    mark: "kalyx",
    line: "Draft recurring updates, announcements, and posts.",
    ask: "I want an application that drafts our recurring updates, announcements, and posts.",
  },
];

const FALLBACK_ROWS: StarterRow[] = STARTERS.map((starter) => ({ kind: "app", ...starter }));

function namedTiles(providers: MissingTile[]): string {
  const labels = providers.map((tile) => tile.label);
  return labels.length < 2
    ? labels.join("")
    : labels.slice(0, -1).join(", ") + " and " + labels.at(-1);
}

function StarterMark({ row }: { row: StarterRow }) {
  if (row.kind === "unlock" && row.providers?.length) {
    return (
      <BrandMark provider={row.providers[0].name} className="size-(--size-glyph) shrink-0" />
    );
  }
  if (row.kind === "check_in" || !row.mark) {
    return <IconMessage className="size-(--size-glyph) shrink-0 text-ink-soft" aria-hidden />;
  }
  return <AgentIcon name={row.mark} className="size-(--size-glyph) shrink-0" />;
}

function Starters({ agentId }: { agentId: string }) {
  const read = usePanelRead<StartersPayload>(STARTERS_READ, 0, STARTERS_EVERY_MS);
  const answered = read.phase === "ready" ? read.payload : null;
  const rows = answered?.starters?.length ? answered.starters : FALLBACK_ROWS;
  const unlock = answered?.unlock?.providers?.length ? answered.unlock : null;
  const start = (target: string | null | undefined, ask: string) => {
    const next = target ?? agentId;
    setPendingAsk(next, ask, true);
    if (next !== agentId) navigate(newChatHash(next));
  };
  return (
    <div className="mt-2xl flex flex-col">
      {rows.map((row) => (
        <PressRow
          key={row.agent_id ?? `${row.kind}:${row.ask}`}
          glyph={<StarterMark row={row} />}
          line={row.line}
          onPress={() => start(row.agent_id, row.ask)}
        />
      ))}
      {unlock ? (
        <PressRow
          glyph={
            <BrandMark
              provider={unlock.providers[0].name}
              className="size-(--size-glyph) shrink-0"
            />
          }
          line={unlock.line}
          note={"Connect " + namedTiles(unlock.providers) + "."}
          onPress={() => start(unlock.agent_id, unlock.ask)}
        />
      ) : (
        <a href={sectionHash("connectors")} className={PRESS_ROW}>
          <IconPlug className="size-(--size-glyph) shrink-0 text-ink-soft" aria-hidden />
          <span className="min-w-0 flex-1 truncate text-ink-soft">
            Connect more accounts for better suggestions.
          </span>
          <IconChevronRight className={PRESS_ROW_CHEVRON} aria-hidden />
        </a>
      )}
    </div>
  );
}
