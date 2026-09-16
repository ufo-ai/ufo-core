import { useEffect, useRef, useState } from "react";

import { IconCheck } from "@tabler/icons-react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
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
import { Meta } from "@/components/ui/meta";
import { AgentIcon } from "@/lib/agentIcon";
import { cn } from "@/lib/cn";
import type { ChatQuestion, QuestionEntry, QuestionOption } from "@/lib/types";

const MAX_ANSWER_OPTIONS = 10;

const MIN_ANSWER_OPTIONS = 2;

export function choosable(entry: QuestionEntry): boolean {
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

export type SettledRow = { entry: QuestionEntry; answer: string };

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

export function Settled({ rows, restated }: { rows: SettledRow[]; restated: boolean }) {
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

/* The card draws these and the thread's foot withholds its rows while any stand. Only the
   transcript read writes `closed`, so a foot reading it alone stayed shut for the session. */
export function openEntries(question: ChatQuestion): { entry: QuestionEntry; index: number }[] {
  const landed = question.answered ?? {};
  return question.closed
    ? []
    : (question.questions ?? [])
        .map((entry, index) => ({ entry, index }))
        .filter(({ index }) => landed[index] === undefined);
}

export function Asked({
  question,
  held,
  onAnswer,
}: {
  question: ChatQuestion;
  held: boolean;
  onAnswer: (answers: FormData, open: { entry: QuestionEntry; index: number }[]) => void;
}) {
  const asked: QuestionEntry[] = question.questions ?? [];
  const landed = question.answered ?? {};
  const open = openEntries(question);
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
            onAnswer(new FormData(event.currentTarget), steppable);
          }}
        >
          {steppable.map(({ entry, index }) => (
            <QuestionnaireItem key={index} name={String(index)} multiple={entry.multi_select ?? undefined}>
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
