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
import { setPendingAsk, takePendingAsk, watchPendingAsk } from "@/lib/pendingAsk";
import { newChatHash, sectionHash } from "@/lib/route";
import { navigate } from "@/lib/router";
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

/** Who a chat is addressed to. A conversation another surface holds names its agent on the rail
 *  row alone, without the boot read listing it, so a chat asks for the facts such a row carries
 *  and never for the whole record. */
export type ChatAgent = {
  id: string;
  name: string;
  model: string;
  icon?: string;
  /** The slug the `app_*` extension shipped this agent under, where one did. The composer reads it
   *  to know whether the app a message addresses is the chat app itself. */
  app?: string | null;
};

export type ChatProps = {
  agent: ChatAgent;
  member: Member;
  conversationId: string | null;
  /** The store key a founding send runs under, when the pane is not the chat screen: the wizard
   *  founds on a key of its own, so its opening send and the chat screen's new-conversation buffer
   *  can never hold each other busy. */
  foundingKey?: string;
  /** What the pane draws over the box while the chat is still unsaid — the chat lane's own history of
   *  conversations. The screen is no longer empty, so the words the start screen says over an empty
   *  pane would stand between the history and the box. */
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
  unsaid,
  onCreated,
  onActivity,
  onSettled,
}: ChatProps) {
  const chatKey = conversationId ?? foundingKey ?? "new:" + agent.id;
  const draftKey = member.id + "/" + chatKey;
  const state = useChat(chatKey);
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
  /** A conversation nobody has said anything in yet. The transcript is the whole screen once one
   *  exists, so until then the suggestions stand directly over the box at the foot of the pane. A
   *  message still loading counts as unsaid, or the first paint would draw the log's layout and the
   *  composer would jump on the frame after it. */
  const starting = conversationId === null && settled && !messages?.length;
  /** Whether the pane opens on the start screen's own words. A caller that hands in what an unsaid
   *  chat draws instead has given the pane something to read, and the box holds the foot under it. */
  const bare = starting && unsaid === undefined;
  const showEmpty = messages !== null && !messages.length && settled;
  const stalled = messages === null ? state.fault : null;
  const credentials = state.handoffs.credentials;
  const held = state.busy || state.messages === null;

  return (
    <TranscriptScroll>
      {starting && !bare ? unsaid : null}
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
        target={target}
        draftKey={draftKey}
        input={composer}
        starting={bare}
        placeholder={conversationId !== null ? FOLLOW_UP_PLACEHOLDER : NEW_CHAT_PLACEHOLDER}
        /* The band names the app a message addresses, which qualifies the words where the app is
           not the surface — a directive chat with Radar reads as Radar's. The chat app is the
           surface, so naming it there states the screen the member is already looking at. */
        eyebrow={agent.app === CHAT_SURFACE ? null : agentName(agent.name)}
        eyebrowIcon={agent.icon}
      />
      <Toast
        state={stalled ? SILENT : state.fault ?? SILENT}
        onDone={() => updateChat(chatKey, (current) => ({ ...current, fault: null }))}
      />
    </TranscriptScroll>
  );
}

/** The state a box that founds a conversation opens on: an empty transcript, so the box is live
 *  rather than waiting on a read there is nothing to read, and no error left standing by a send that
 *  failed before this one. Every screen that draws that box establishes it — the start screen and
 *  the chat app's list both — so it is stated here once. */
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
        target={target}
        draftKey={draftKey}
        input={composer}
        starting={false}
        placeholder={NEW_CHAT_PLACEHOLDER}
        eyebrow={agent.app === CHAT_SURFACE ? null : agentName(agent.name)}
        eyebrowIcon={agent.icon}
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
 *  hidden, it would leave them with no record of what they chose, and the card is that record: the
 *  answers stand here rather than as bubbles of their own further down the transcript.
 *
 *  A card the conversation has moved past is that record and nothing else. The turn it asked in is
 *  over, so every entry reads as what the member chose and none of them offers a control — an
 *  answer now would name an ask the conversation cannot take. */
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
          onItemChange={(name) => {
            // A step the member moved by hand — the stepper, Skip, an invalid jump — outranks a
            // press still waiting out its beat: firing it after would carry them somewhere they
            // just chose to leave.
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
                        // The pointer's press, never the click alone: arrowing through a radio
                        // group fires a click on every option the arrows pass — detail 0, no
                        // pointer under it — and a run that moved on those carried a keyboard
                        // member off the question mid-read. A letter-pressed answer stays put the
                        // same way; Enter is the keyboard's own way on.
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
                {/* A multi-select's answers post joined under one name, so words typed beside its
                    boxes would read back as one more label no option holds — the boxes are the
                    whole form there, and words go to the composer. Everywhere else the row is an
                    answer like any other. */}
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

/** Each answer is admitted against the entry it answers, in the order they were asked, because
 *  the turn recorded them as separate questions and reads them back the same way. An entry the
 *  member skipped says nothing rather than saying nothing at length. A single question needs no
 *  restatement; one of several names itself, so the words the turn reads name what they answer. */
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

const COMPOSER_LABEL = "Ask UFO";
/** What the box says wherever it founds a conversation rather than joining one — the start screen
 *  and the box under a history both: the words a member writes there open a conversation. */
const NEW_CHAT_PLACEHOLDER = "Start new chat…";
/** What the box says in a conversation the member is reading: the transcript over it is what the
 *  next words carry on from. */
const FOLLOW_UP_PLACEHOLDER = "Ask a follow-up…";
/** What the start screen says before the member has said anything. The screen is otherwise empty,
 *  and a pane that opens on nothing states nothing about what it is for. */
const START_INTRO = "What can UFO do for you?";

/** The message box holds as many lines as the member writes. Enter sends it and Shift+Enter opens
 *  a line, which is the pairing every chat composer ships and the only one a member arrives
 *  already knowing; a composition in flight (an IME candidate) takes its own Enter, so the guard
 *  reads `isComposing` before claiming the key. */
function Composer({
  target,
  draftKey,
  input,
  starting,
  placeholder,
  eyebrow,
  eyebrowIcon,
}: {
  target: ChatTarget;
  draftKey: string;
  input: RefObject<HTMLTextAreaElement | null>;
  starting: boolean;
  placeholder: string;
  eyebrow: string | null;
  eyebrowIcon?: string;
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
    const handed = founding ? takePendingAsk(target.agentId, target.key) : null;
    if (handed?.send) committed.current = handed.text;
    return handed?.text ?? readDraft(draftKey);
  });
  const [stopping, setStopping] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const showsEyebrow = eyebrow !== null && !dismissed;
  // The turn the page is tailing, and so the one a stop can name. A send holds the chat busy before
  // admission answers with a turn id, and a stop of a turn nobody has named yet reaches nothing.
  const running = state.turn;
  // Live during a turn, so a correction reaches the agent mid-reply. The exception is the send that
  // founds a conversation: until it answers there is no conversation for a second message to join,
  // and these words stay in the box rather than opening a conversation of their own.
  const disabled = state.messages === null || (target.conversationId === null && state.busy);

  // A route that renames this composer's agent — the rail's "New conversation" names the main one
  // from another agent's start screen — renames the draft too, and no mount comes to read the named
  // agent's own words. The box reads them here: the words it was holding are already stored under
  // the agent they were written for (the flush lands any write still pending), and a pick in the
  // box's own toolbar carries its words to the new key first, so this read hands back the same text
  // and the member's place in it stands. The mount's own read is the initializer's, where a pending
  // ask outranks the stored draft.
  const drafted = useRef(draftKey);
  useEffect(() => {
    if (drafted.current === draftKey) return;
    drafted.current = draftKey;
    flushDrafts();
    setText(readDraft(draftKey));
  }, [draftKey]);

  // An ask handed over while this composer is already mounted — a starter and the palette both hand
  // one from the chat screen itself — reaches the box here rather than waiting for a mount that
  // never comes. The palette sets the ask on the agent it names and then routes to that agent's
  // start screen, which renames the agent of this same composer, so the take runs again on the new
  // name: the one start screen stands for whichever agent it names, and no mount comes to read the
  // ask.
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

  async function send(attached: File[]): Promise<boolean> {
    const trimmed = text.trim();
    if ((!trimmed && !attached.length) || disabled) return false;
    setText("");
    clearDraft(draftKey);
    // The bytes go to the blob store first, so the send body names the keys rather than carrying
    // the files. A file the store took no URL for — a dev deploy that signs none, an upload that
    // failed — rides the multipart body it always has.
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
    toTheFoot();
    // The bubble states the words and draws the files over them, so a send of files alone shows the
    // files rather than a line of their names.
    void sendMessage(target, body, trimmed, attached);
    return true;
  }

  const box = (
    <PromptInput onSend={send}>
      {showsEyebrow ? (
        <PromptInputEyebrow
          glyph={
            eyebrowIcon ? (
              <AgentIcon name={eyebrowIcon} className="size-(--size-glyph) shrink-0" />
            ) : null
          }
          label={eyebrow}
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
  // On the start screen the suggestions and box stand at the foot: nothing else is on that screen,
  // so a press in the empty space around the card is a press on the words the member came to write,
  // and it lands the cursor in them rather than on nothing. A press on the card itself is the
  // card's own — the box places its cursor where the member pressed, and the controls beside it
  // take their own presses.
  //
  // Both screens are the same two elements around the box, holding the whole start screen off the
  // card in the outer one. Two shapes would rebuild the box on the send that opens the
  // conversation, dropping what the member attached to that message and their place in the words.
  return (
    <div
      data-testid={starting ? "start" : undefined}
      className={starting ? "flex flex-1 flex-col justify-between overflow-y-auto" : undefined}
      onMouseDown={
        starting
          ? (event) => {
              if ((event.target as Element).closest("[data-field-card]")) return;
              event.preventDefault();
              input.current?.focus();
            }
          : undefined
      }
    >
      {starting ? (
        <div className={cn(COLUMN, "px-2xl pt-lg")}>
          <p className="m-0 text-ui text-ink-soft">{START_INTRO}</p>
        </div>
      ) : null}
      <div
        className={cn(
          COLUMN,
          "px-2xl pt-lg pb-[max(var(--spacing-lg),env(safe-area-inset-bottom))]",
        )}
      >
        {starting ? <Starters agentId={target.agentId} /> : null}
        {box}
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
/** The slate behind this read turns over on the half hour, and the read that finds it stale is the
 *  one that pays to remake it. Polling it at the panel default would ask six times an hour for an
 *  answer that changes twice. */
const STARTERS_EVERY_MS = 300_000;

/** What a member can ask for before they have asked for anything: three applications named for the
 *  job each does and the decision each leaves with them. Creating one is the same act whichever app
 *  is being asked, so every start screen carries them. The body states that decision in the measure
 *  a row leaves beside the name, which is one line.
 *
 *  These are what the screen says where the read has nothing to say — before it lands, where it
 *  fails, and in a workspace whose memory has yet to name any work of its own. A member is never
 *  shown an empty screen or a spinner where their own work will stand: the rows are one height, so
 *  what lands replaces words and moves nothing. */
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

/** A press is the whole act: the starter's sentence is said and the conversation opens on it. The
 *  member chose these words by pressing them, the way they choose the palette's row, and the
 *  sentence commits nothing but itself — what it asks for is decided later, in the conversation it
 *  opens.
 *
 *  They stack over the box, a row each, at the height the sidebar's own rows keep, so the list
 *  reads as destinations rather than as cards set into the screen. A row says one sentence and is
 *  one line: what the measure cannot hold is cut, never wrapped, so three starters cost three
 *  lines whatever they say.
 *
 *  An application row wears the mark the sidebar draws that app under, at the size the sidebar
 *  draws it, because that is what a press founds: the row states the shape of the thing, not a
 *  category glyph standing in for it. A check-in founds no app — it asks after work already under
 *  way — so it wears a glyph of the same width, and every row's words still start on one edge.
 *
 *  The connectors row closes the stack. Where the read names an unlock it is an ask like the three
 *  above it: the member says what they want built, and the agent asks for the accounts it turns out
 *  not to hold — the connect control rides its reply, so the row orchestrates nothing and its words
 *  state the price rather than a destination. It wears the brand mark of the first account it
 *  names. Where the read names none, it falls back to the link to the screen where accounts are
 *  connected, the one row that is not an ask. Either way it keeps the same rule and the same
 *  arrow.
 *
 *  The arrow is drawn under the pointer or the focus outline rather than at rest: arrows standing on
 *  an idle screen say a row leads somewhere once per row. It holds its place while hidden, so the
 *  sentence truncates at the same character whether the row is under the pointer or not. */
const FALLBACK_ROWS: StarterRow[] = STARTERS.map((starter) => ({ kind: "app", ...starter }));

/** The accounts an unlock names, said the way a person says them. */
function namedTiles(providers: MissingTile[]): string {
  const labels = providers.map((tile) => tile.label);
  return labels.length < 2
    ? labels.join("")
    : labels.slice(0, -1).join(", ") + " and " + labels.at(-1);
}

function StarterMark({ row }: { row: StarterRow }) {
  // An unlock drawn in an application's slot wears the brand of the account it still needs, the
  // way the connector row does. The row says its work and not its price, so the mark is the whole
  // of what states the account before the conversation opens.
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

/** A row is one line, and the sentence it holds is the member's own work said back to them, so a
 *  measure too narrow to hold it must not be where it goes unread: the cut sentence travels under
 *  the pointer until its last word has been read. A row that already fits stands still — a line
 *  moving under a sentence the eye has just read whole is noise the member did not ask for. */
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
    <div className="mb-2xl flex flex-col">
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
