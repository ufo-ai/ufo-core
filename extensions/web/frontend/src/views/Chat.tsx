import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";

import { IconCreditCardOff } from "@tabler/icons-react";

import { CredentialPromptForm } from "@/views/CredentialPrompt";
import { Asked, choosable, openEntries } from "@/components/ui/asked";
import { buttonVariants } from "@/components/ui/button";
import { Handoff } from "@/components/ui/handoff";
import { OfferRows } from "@/components/ui/offers";
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
import {
  FALLBACK_ROWS,
  Starters,
  Wordmark,
  type StarterRow,
  type UnlockRow,
} from "@/components/ui/starters";
import { SILENT, Toast } from "@/components/ui/toast";
import { Watching } from "@/components/ui/watching";
import {
  MessageLog,
  TranscriptPane,
  TranscriptScroll,
  useTakeMeToTheFoot,
} from "@/kernel/messages";
import { takeFocus } from "@/kernel/focus";
import { COLUMN } from "@/kernel/pane";
import { Empty, usePanelRead } from "@/kernel/panel";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { useAppStatus } from "@/lib/appStatusStore";
import { useMe } from "@/lib/audience";
import { cn } from "@/lib/cn";
import {
  attachedTurn,
  chatState,
  clearChat,
  updateChat,
  useChat,
  type ChatTurn,
} from "@/lib/chatStore";
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
import { newChatHash, workspaceHash } from "@/lib/route";
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
import type { ChatQuestion, Member, QuestionEntry } from "@/lib/types";

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
  /** Draw the transcript and follow its live turn, with no composer, no question to answer, and no
   *  credential form: a screen for watching a conversation rather than speaking in it. */
  readOnly?: boolean;
  /** The one turn a read-only screen may stop. Stop draws only while that turn is the live one, so
   *  a drawer on a settled run never ends the run that came after it in the same conversation. */
  stops?: string;
  /** The run the member arrived on, whose own words the transcript stands at and marks. A screen
   *  reached by address takes the run off the address instead. */
  focusRun?: string;
  unsaid?: ReactNode;
  onCreated?: (conversationId: string, title: string) => void;
  onActivity?: (conversationId: string, turn: ChatTurn) => void;
  onSettled?: () => void;
};

export function Chat({
  agent,
  member,
  conversationId,
  foundingKey,
  focusComposer = false,
  readOnly = false,
  stops,
  focusRun,
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
    onAccepted: (accepted) => onActivity?.(accepted, "running"),
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
    if (wasBusy.current && !state.busy) {
      onSettled?.();
      if (conversationId !== null && state.ended !== null) {
        onActivity?.(conversationId, state.ended);
      }
    }
    wasBusy.current = state.busy;
  }, [conversationId, onActivity, onSettled, state.busy, state.ended]);

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
  /* The pick is this thread's — it stands while the agent's own model moves under it, and the rows
     under the thread send on it as the composer does, so it lives above both rather than in one. */
  const [picked, setPicked] = useState<string | null>(null);
  const pinned = useRef(agent.id);
  useEffect(() => {
    if (pinned.current === agent.id) return;
    pinned.current = agent.id;
    setPicked(null);
  }, [agent.id]);
  const settling = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(settling.current), []);
  const animateTheSend = () => {
    setAnimate(true);
    window.clearTimeout(settling.current);
    settling.current = window.setTimeout(() => setAnimate(false), SEND_SCROLL_MS);
  };
  const stalled = messages === null ? state.fault : null;
  const credentials = state.credentials;
  const watched = attachedTurn(state);
  const held = state.busy || state.messages === null;
  /* A card stops asking three ways: the transcript closes it, the member answers it, or they say
     something else instead — an ask the member spoke past is a choice they no longer have. */
  const spoken = messages ?? [];
  const at = spoken.reduce((found, said, index) => (said.question ? index : found), -1);
  const asking =
    at >= 0 &&
    openEntries(spoken[at].question!).length > 0 &&
    !spoken.slice(at + 1).some((said) => said.role === "user");
  /* The rows answer the thread's newest turn, which the surface reads for itself — so what asks for
     them again is a message landing, not a turn id the live path has yet to learn. */
  const said = messages?.length ?? 0;
  const offering =
    !readOnly && conversationId !== null && settled && !credentials && !asking && said > 0;

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
            focus={
              focusRun ??
              (route.kind === "chat" && route.conversationId === conversationId
                ? (route.run ?? null)
                : null)
            }
            className={cn(COLUMN, "p-2xl")}
            question={
              readOnly
                ? undefined
                : (question) => (
                    <Answering
                      target={target}
                      question={question}
                      held={held}
                      onAct={() => composer.current?.focus()}
                    />
                  )
            }
          >
            {credentials && !readOnly ? (
              <Handoff>
                <div>{credentials.reason}</div>
                {credentials.prompts.map((prompt) => (
                  <CredentialPromptForm
                    key={prompt.slot}
                    sealed={credentials.sealed}
                    prompt={prompt}
                    onStored={(slot) =>
                      updateChat(chatKey, (current) => {
                        const request = current.credentials;
                        if (!request) return current;
                        return {
                          ...current,
                          credentials: {
                            ...request,
                            prompts: request.prompts.map((entry) =>
                              entry.slot === slot ? { ...entry, stored: true } : entry,
                            ),
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
            {offering ? (
              <FollowUps
                key={said}
                agentId={agent.id}
                conversationId={conversationId}
                onPress={(prompt) => {
                  animateTheSend();
                  void sendMessage(live.current, prompt, prompt, [], picked);
                }}
              />
            ) : null}
          </MessageLog>
        </TranscriptPane>
      )}
      {readOnly ? (
        watched !== null && watched === stops ? (
          <Watching onStop={() => stopTurn(target, watched)} />
        ) : null
      ) : (
        <Composer
          agent={agent}
          target={target}
          draftKey={draftKey}
          input={composer}
          starting={bare}
          picked={picked}
          /* A turn pins a concrete id, so the Auto row carries no pin and leaves the agent's own
             model to resolve per turn. */
          onPick={(chosen) =>
            setPicked(chosen === AUTO_MODEL || chosen === agent.model ? null : chosen)
          }
          onSent={animateTheSend}
          placeholder={conversationId !== null ? FOLLOW_UP_PLACEHOLDER : NEW_CHAT_PLACEHOLDER}
        />
      )}
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
  const [picked, setPicked] = useState<string | null>(null);
  const pinned = useRef(agent.id);
  useEffect(() => {
    if (pinned.current === agent.id) return;
    pinned.current = agent.id;
    setPicked(null);
  }, [agent.id]);
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
        picked={picked}
        onPick={(chosen) =>
          setPicked(chosen === AUTO_MODEL || chosen === agent.model ? null : chosen)
        }
      />
    </TranscriptScroll>
  );
}

function Answering({
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
  const toTheFoot = useTakeMeToTheFoot();
  return (
    <Asked
      question={question}
      held={held}
      onAnswer={(answers, open) => {
        onAct();
        toTheFoot();
        void deliver(target, question, question.questions ?? [], open, answers);
      }}
    />
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
    const options = entry.options ?? [];
    return [
      {
        index,
        body: asked.length === 1 ? joined : joined + " · " + entry.question,
        picked:
          choosable(entry) &&
          values.every((value) => options.some((option) => option.label === value)),
      },
    ];
  });
  await answerQuestions(target, question.turn_id, given);
}

/** How long the transcript stays animated after a send: long enough for the anchor scroll the send
 *  causes to finish, short enough that a reply landing just after it still places instantly. */
const SEND_SCROLL_MS = 700;

const COMPOSER_LABEL = "Ask UFO";
const NEW_CHAT_PLACEHOLDER = "Start new chat…";
const FOLLOW_UP_PLACEHOLDER = "Ask a follow-up…";
/* No resume is promised: a scheduled fire is cancelled and its occurrence consumed (`_refused` in
   `admission.py`, `reschedule` in `schedules.py`). Billing answers an admin alone, hence one link. */
const OUT_OF_CREDIT_ADMIN = "Out of credit. All work has stopped.";
const OUT_OF_CREDIT_MEMBER = "Out of credit. Ask your admin to add credit.";
const BILLING_LINK = "Go to billing";

/** A composition in flight — an IME candidate — takes its own Enter, so the guard reads `isComposing`
 *  before claiming the key. */
function Composer({
  agent,
  target,
  draftKey,
  input,
  starting,
  placeholder,
  picked,
  onPick,
  onSent,
}: {
  agent: ChatAgent;
  target: ChatTarget;
  draftKey: string;
  input: RefObject<HTMLTextAreaElement | null>;
  starting: boolean;
  placeholder: string;
  picked: string | null;
  onPick: (chosen: string) => void;
  onSent?: () => void;
}) {
  const state = useChat(target.key);
  const founding = target.conversationId === null;
  const committed = useRef<string | null>(null);
  const pressed = useRef<string | null>(null);
  const [text, setText] = useState(() => {
    const handed = founding ? takePendingAsk(target.agentId, target.key) : null;
    if (handed?.send) {
      committed.current = handed.text;
      pressed.current = handed.starter;
    }
    return handed?.text ?? readDraft(draftKey);
  });
  const [stopping, setStopping] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const model = picked ?? agent.model;
  /** A conversation's agent reaches this box as a `ConversationAgent`, which carries no `app`, so the
   *  chat surface arrives named but unclassed. */
  const addressed = [agent.app, agent.name].includes(CHAT_SURFACE) ? null : agentName(agent.name);
  const { outOfCredit } = useAppStatus();
  const admin = useMe()?.admin === true;
  const showsEyebrow = addressed !== null && !dismissed;
  const running = attachedTurn(state);
  const disabled = state.messages === null || (target.conversationId === null && state.busy);

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
      if (handed.send) {
        committed.current = handed.text;
        pressed.current = handed.starter;
      }
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
    const starter = pressed.current;
    pressed.current = null;
    input.current?.focus();
    onSent?.();
    void sendMessage(target, body, trimmed, attached, picked, starter);
    return true;
  }

  const box = (
    <PromptInput onSend={send}>
      {outOfCredit ? (
        <PromptInputEyebrow
          tone="attention"
          glyph={<IconCreditCardOff className="size-(--size-glyph) shrink-0" />}
          label={admin ? OUT_OF_CREDIT_ADMIN : OUT_OF_CREDIT_MEMBER}
          action={
            admin ? (
              <a
                href={workspaceHash("billing")}
                className={buttonVariants({ variant: "quiet" }) + " no-underline"}
              >
                {BILLING_LINK}
              </a>
            ) : null
          }
        />
      ) : showsEyebrow ? (
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
          <PromptInputModel model={model} onPick={onPick} />
          <PromptInputSubmit
            stops={Boolean(running && state.busy && !text.trim())}
            busy={stopping}
            disabled={disabled}
            onStop={() => {
              if (stopping || !running) return;
              void stop(running);
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
        {starting ? <Wordmark /> : null}
        {box}
        {starting ? <RankedStarters agentId={target.agentId} /> : null}
      </div>
    </div>
  );
}

type OfferKind = "ask" | "keep" | "share" | "watch";
type Offer = { kind: OfferKind; hook: string; prompt: string };
type OffersPayload = { offers: Offer[]; ranking: boolean };

/* Nothing but a new turn changes what this read says, and a new turn mounts a pane of its own, so
   the interval is the tab's long stop rather than how the rows arrive. */
const FOLLOW_UPS_EVERY_MS = 600_000;
/* Except while another read is writing this turn's rows, which is over in a second or two. */
const FOLLOW_UPS_RANKING_MS = 2_000;

function FollowUps({
  agentId,
  conversationId,
  onPress,
}: {
  agentId: string;
  conversationId: string;
  onPress: (prompt: string) => void;
}) {
  const [ranking, setRanking] = useState(false);
  const answered = usePanelRead<OffersPayload>(
    "/agents/" + agentId + "/conversations/" + conversationId + "/follow-ups",
    0,
    ranking ? FOLLOW_UPS_RANKING_MS : FOLLOW_UPS_EVERY_MS,
  );
  const read = answered.phase === "ready" ? answered.payload : null;
  useEffect(() => setRanking(read?.ranking ?? false), [read]);
  return <OfferRows offers={read?.offers ?? []} onPress={onPress} />;
}

type StartersPayload = { starters: StarterRow[]; unlock: UnlockRow | null };

const STARTERS_READ = "/workspace/starters";
const STARTERS_EVERY_MS = 300_000;

function RankedStarters({ agentId }: { agentId: string }) {
  const read = usePanelRead<StartersPayload>(STARTERS_READ, 0, STARTERS_EVERY_MS);
  const answered = read.phase === "ready" ? read.payload : null;
  const rows = answered?.starters?.length ? answered.starters : FALLBACK_ROWS;
  const unlock = answered?.unlock?.providers?.length ? answered.unlock : null;
  return (
    <Starters
      rows={rows}
      unlock={unlock}
      waiting={read.phase === "loading"}
      onStart={(target, ask, kind) => {
        const next = target ?? agentId;
        setPendingAsk(next, ask, true, null, kind);
        if (next !== agentId) navigate(newChatHash(next));
      }}
    />
  );
}
