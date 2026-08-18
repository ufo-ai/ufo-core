import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";

import { IconCheck } from "@tabler/icons-react";

import { CredentialPromptForm } from "@/views/CredentialPrompt";
import { AgentPicker } from "@/kernel/agentpick";
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
import { AgentIcon } from "@/lib/agentIcon";
import { cn } from "@/lib/cn";
import { chatState, clearChat, updateChat, useChat } from "@/lib/chatStore";
import { clearDraft, installDraftFlush, moveDraft, readDraft, writeDraft } from "@/lib/drafts";
import { useEarlierMessages } from "@/lib/earlier";
import { setPendingAsk, takePendingAsk, watchPendingAsk } from "@/lib/pendingAsk";
import {
  answerQuestions,
  refreshTranscript,
  resyncChat,
  sendMessage,
  stopTurn,
  type ChatTarget,
} from "@/lib/turnStream";
import type { Agent, ChatQuestion, Member, QuestionEntry, QuestionOption } from "@/lib/types";

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

/** The fewest options a question is answered by choosing between. One option is nothing to choose
 *  between: a control offering it states an answer the member can only agree with, so the words are
 *  drawn as the answer the question opens with and they change them. */
const MIN_ANSWER_OPTIONS = 2;

/** Whether an entry's options are drawn as choices at all. A question that will take a file, and a
 *  list too long to read, are both the message box's job — the form can hold neither, and offering
 *  a control that cannot carry the answer is worse than saying where the answer goes. */
function choosable(entry: QuestionEntry): boolean {
  return Boolean(
    entry.options &&
      entry.options.length >= MIN_ANSWER_OPTIONS &&
      entry.options.length <= MAX_ANSWER_OPTIONS &&
      !entry.free_text_only &&
      !entry.allow_attachments,
  );
}

/** Answered by typing into the form: words, and no file to go with them. */
function typed(entry: QuestionEntry): boolean {
  return (
    !entry.allow_attachments &&
    (Boolean(entry.free_text_only) || (entry.options ?? []).length < MIN_ANSWER_OPTIONS)
  );
}

/** The keys the answers are drawn under and pressed by, in the order they are read: the choices
 *  take the first of them, and the row the member types into takes the one after. */
const KEYS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

type SettledRow = { entry: QuestionEntry; answer: string };

/** The key one answer is drawn under: the choice's own place in the list, or the row after the
 *  last of them for words the member typed. One rule, so the key a settled answer states is the
 *  key the member pressed to give it — options a question never drew as choices take no key. */
function answerKey(entry: QuestionEntry, words: string): string {
  const options = choosable(entry) ? (entry.options ?? []) : [];
  const at = options.findIndex((option) => option.label === words);
  return KEYS[at === -1 ? options.length : at];
}

/** How long a pressed answer stands before the run moves on: long enough to read as chosen, short
 *  enough that the member is not waiting on it. A press that lands on the last question moves
 *  nowhere, and a question taking several answers waits for the member to say they are done.
 *
 *  The press is what moves the run, never the selection: arrowing through a radio group selects
 *  each option it passes, and a member reading the options that way would be carried off the
 *  question mid-read. They go on with the act that says so. */
const ANSWERED_MS = 320;

/** The answer a question opens with, wherever the turn already holds it: the words the member's own
 *  message settled, or the one option a question offering a single answer proposes — those words
 *  are the proposal itself, not a choice, since there is nothing to weigh them against. */
function opened(entry: QuestionEntry): string | undefined {
  const options = entry.options ?? [];
  if (entry.chosen) return entry.chosen;
  const lone = options.length === 1 && !entry.free_text_only && !entry.allow_attachments;
  return lone ? options[0].label : undefined;
}

/** Whether the form already holds this entry's answer, so opening the run on it would ask the
 *  member for what the step is showing them. A question taking several answers is never settled
 *  this way: the member says when they are done choosing. */
function already(entry: QuestionEntry): boolean {
  return !entry.multi_select && opened(entry) !== undefined;
}

/** What the row the member types into opens with: the answer already settled, wherever no drawn
 *  choice carries it. A member who has already said what they want reads it back and changes it
 *  rather than being asked for it again. */
function written(entry: QuestionEntry): string | undefined {
  const answer = opened(entry);
  if (choosable(entry) && (entry.options ?? []).some((option) => option.label === answer)) {
    return undefined;
  }
  return answer;
}

/** The options a typed question lists beneath it: what an answer may say, where the form takes
 *  words rather than a choice. An option the row already opens with is left out — listed as well,
 *  it would state the same words twice. */
function suggested(entry: QuestionEntry): QuestionOption[] {
  const options = entry.options ?? [];
  return options.filter((option) => option.label !== written(entry));
}

/** Everything the member has answered, drawn as one filled block: each question as it was asked
 *  and under it the one answer they gave, in the soft ink of a record rather than a control, each
 *  row closed by a check. One block rather than one per question — the run is over and these are
 *  its summary, so a gap between them would state a boundary the answers do not have. Nothing here
 *  is pressable: the answers are admitted and the turn has them, so a row that still looked like a
 *  control would offer a change the conversation cannot take. */
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

/** What a turn asks, drawn as one card under the reply that asked it: what the turn is about and
 *  the mark of the agent asking, then the question, its answers, and the acts that commit them.
 *  The card is the boundary of the ask — everything inside it is the turn waiting on the member,
 *  and the transcript reads on beneath it.
 *
 *  It is taken one question at a time. A turn may ask up to four things; four of them stacked in a
 *  transcript is a wall the member has to read before answering any of it, and the answers to the
 *  later ones often depend on the earlier. A step names one decision, says where it sits in the
 *  run, and can be gone back to.
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
  const settled = asked
    .map((entry, index) => ({ entry, answer: landed[index] }))
    .filter((row): row is SettledRow => row.answer !== undefined);
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
  const names = items.map((item) => item.name);
  // The run opens on the first question the turn does not already have the answer to, and a pressed
  // answer moves it to the next of those: a step whose answer the member's own words settled asks
  // them to confirm what it is showing, so the run passes it and they reach it with the stepper.
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
          onItemChange={setAt}
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
                        onClick={() => {
                          if (entry.multi_select) return;
                          const next = onward(names.indexOf(String(index)));
                          if (next === undefined) return;
                          clearTimeout(moving.current ?? undefined);
                          moving.current = setTimeout(() => setAt(next), ANSWERED_MS);
                        }}
                      >
                        <span className="shrink-0">{option.label}</span>
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
                <QuestionnaireInput
                  aria-label={entry.question}
                  placeholder="Your answer"
                  shortcut={KEYS[choosable(entry) ? (entry.options ?? []).length : 0]}
                  defaultValue={written(entry)}
                />
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

  // An ask handed over while this composer is already mounted — a starter and the palette both hand
  // one from the chat screen itself — reaches the box here rather than waiting for a mount that
  // never comes.
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
    let body: string | FormData = trimmed;
    if (attached.length) {
      const form = new FormData();
      form.set("message", trimmed);
      for (const file of attached) form.append("file", file);
      body = form;
    }
    input.current?.focus();
    toTheFoot();
    // The bubble states the words and draws the files over them, so a send of files alone shows the
    // files rather than a line of their names.
    void sendMessage(target, body, trimmed, attached);
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
      {starting ? <Starters agentId={target.agentId} /> : null}
    </div>
  );
}

/** What a member can ask for before they have asked for anything: three applications named for the
 *  job each does and the decision each leaves with them. Creating one is the same act whichever app
 *  is being asked, so every start screen carries them. */
const STARTERS = [
  {
    title: "Inbox triage",
    body: "Reads new mail and drafts a few replies for each. You pick one, and nothing sends itself.",
    ask: "I want an application that works my inbox: read new mail, draft a few replies for each one, and send nothing without me.",
  },
  {
    title: "Research routing",
    body: "Reads the sources you name, keeps what matters, and routes each lead to whoever owns it.",
    ask: "I want an application that reads the sources I name, keeps the findings worth acting on, and routes each one to whoever owns it.",
  },
  {
    title: "Draft review",
    body: "Holds drafts against your own voice and publishes nothing until you approve it.",
    ask: "I want an application that reviews my drafts against how I actually write, and publishes nothing until I approve it.",
  },
];

/** A press is the whole act: the starter's sentence is said and the conversation opens on it. The
 *  member chose these words by pressing them, the way they choose the palette's row, and the
 *  sentence commits nothing but itself — what it asks for is decided later, in the conversation it
 *  opens. */
function Starters({ agentId }: { agentId: string }) {
  return (
    <div className="mt-2xl grid grid-cols-3 gap-lg max-narrow:grid-cols-1">
      {STARTERS.map((starter) => (
        <button
          key={starter.title}
          type="button"
          onClick={() => setPendingAsk(agentId, starter.ask, true)}
          className={cn(
            "flex flex-col gap-xs rounded-panel border border-edge bg-transparent px-lg py-md",
            "text-start text-inherit hover:bg-fill",
          )}
        >
          <span className="text-ui font-medium">{starter.title}</span>
          <span className="text-small text-ink-soft">{starter.body}</span>
        </button>
      ))}
    </div>
  );
}

