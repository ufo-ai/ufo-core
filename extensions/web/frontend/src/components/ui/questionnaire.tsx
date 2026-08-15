import type { ComponentProps } from "react";
import { Questionnaire as QuestionnairePrimitive } from "@shadcn/react/questionnaire";

import { buttonVariants } from "@/components/ui/button";
import { CONTROL } from "@/components/ui/field";
import { cn } from "@/lib/cn";

export type { QuestionnaireItemDefinition, QuestionnaireItemStatus } from "@shadcn/react/questionnaire";

/** What a turn asks of the member, taken one question at a time. It is a real form: every answer
 *  is a native control with a native name, so what the member chose survives a reload, reads back
 *  to assistive technology as the radio or checkbox it is, and arrives as form data rather than as
 *  state some component was holding.
 *
 *  Asking one at a time is the point. A turn may ask up to four things, and four stacked questions
 *  in a transcript is a wall the member has to parse before answering any of it; a step names one
 *  decision, says where it sits in the run, and lets them go back. */
export function Questionnaire({
  className,
  ...props
}: ComponentProps<typeof QuestionnairePrimitive.Root>) {
  return (
    <QuestionnairePrimitive.Root
      data-slot="questionnaire"
      className={cn("flex w-full min-w-0 flex-col gap-2xl", className)}
      {...props}
    />
  );
}

/** Where the member is in the run. It holds one width whatever the numbers are, so the question
 *  beneath it does not shift as the count climbs. */
export function QuestionnaireProgress({
  className,
  ...props
}: ComponentProps<typeof QuestionnairePrimitive.Progress>) {
  return (
    <QuestionnairePrimitive.Progress
      data-slot="questionnaire-progress"
      className={cn(
        "w-fit min-w-(--container-progress) font-mono text-small font-medium tabular-nums text-ink-soft",
        className,
      )}
      {...props}
    />
  );
}

/** One question. It is a fieldset, so the title is its legend and the whole group is named by it
 *  rather than by a paragraph that happens to sit above. */
export function QuestionnaireItem({
  className,
  ...props
}: ComponentProps<typeof QuestionnairePrimitive.Item>) {
  return (
    <QuestionnairePrimitive.Item
      data-slot="questionnaire-item"
      className={cn("flex min-w-0 flex-col gap-2xl border-0 p-0 outline-none", className)}
      {...props}
    />
  );
}

export function QuestionnaireTitle({
  className,
  ...props
}: ComponentProps<typeof QuestionnairePrimitive.Title>) {
  return (
    <QuestionnairePrimitive.Title
      data-slot="questionnaire-title"
      className={cn(
        "text-ui leading-chrome font-medium text-pretty",
        "[&:not(:has(~[data-slot=questionnaire-description]))]:mb-2xl",
        className,
      )}
      {...props}
    />
  );
}

export function QuestionnaireDescription({
  className,
  ...props
}: ComponentProps<typeof QuestionnairePrimitive.Description>) {
  return (
    <QuestionnairePrimitive.Description
      data-slot="questionnaire-description"
      className={cn("text-label text-pretty text-ink-soft", className)}
      {...props}
    />
  );
}

export function QuestionnaireChoices({
  className,
  ...props
}: ComponentProps<typeof QuestionnairePrimitive.Choices>) {
  return (
    <QuestionnairePrimitive.Choices
      data-slot="questionnaire-choices"
      className={cn("group/questionnaire-choices grid min-w-0 gap-sm", className)}
      {...props}
    />
  );
}

/** One answer, drawn as the whole row rather than as a dot with a label beside it: the member
 *  presses anywhere in it. The native control is laid over the row invisibly and still carries the
 *  focus, the name and the value, so nothing here reimplements what a radio already is. Whether
 *  the mark is a circle or a box is read off the control's own type, never passed in twice. */
export function QuestionnaireChoice({
  children,
  className,
  ...props
}: ComponentProps<typeof QuestionnairePrimitive.Choice>) {
  return (
    <QuestionnairePrimitive.Choice
      data-slot="questionnaire-choice"
      className={cn(
        "group/questionnaire-choice relative flex min-h-(--size-touch) cursor-pointer items-start gap-md",
        "rounded-panel border border-edge bg-transparent px-lg py-md",
        "text-start text-ui transition-colors outline-none select-none hover:bg-fill",
        "has-[>input:focus-visible]:border-edge-strong",
        "data-checked:border-primary data-checked:bg-fill",
        "data-invalid:border-attention-ink",
        "data-disabled:pointer-events-none data-disabled:cursor-not-allowed",
        "data-disabled:opacity-(--disabled)",
        className,
      )}
      {...props}
    >
      <QuestionnairePrimitive.ChoiceInput
        data-slot="questionnaire-choice-input"
        className="absolute inset-0 z-10 size-full cursor-pointer opacity-0"
      />
      <span
        aria-hidden
        data-slot="questionnaire-choice-indicator"
        className={cn(
          "pointer-events-none relative flex size-(--size-glyph) shrink-0 translate-y-(--spacing-hair)",
          "items-center justify-center rounded-sm border border-edge",
          "group-data-[type=radio]/questionnaire-choice:rounded-full",
          "group-data-checked/questionnaire-choice:border-primary",
          "group-data-checked/questionnaire-choice:bg-primary",
          "group-data-checked/questionnaire-choice:text-primary-foreground",
        )}
      >
        <span
          data-slot="questionnaire-choice-indicator-dot"
          className={cn(
            "hidden size-sm rounded-full bg-primary-foreground",
            "group-data-[type=checkbox]/questionnaire-choice:hidden",
            "group-data-checked/questionnaire-choice:block",
          )}
        />
        <svg
          viewBox="0 0 16 16"
          data-slot="questionnaire-choice-indicator-check"
          className={cn(
            "hidden size-(--size-glyph)",
            "group-data-[type=radio]/questionnaire-choice:hidden",
            "group-data-checked/questionnaire-choice:block",
          )}
        >
          <path
            d="m4 8.5 2.5 2.5L12 5.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.75"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
      <QuestionnairePrimitive.ChoiceLabel
        data-slot="questionnaire-choice-label"
        className="flex min-w-0 flex-1 flex-col gap-hair leading-chrome"
      >
        {children}
      </QuestionnairePrimitive.ChoiceLabel>
      <QuestionnairePrimitive.ChoiceShortcut
        data-slot="questionnaire-choice-shortcut"
        className={cn(
          "pointer-events-none ms-auto hidden size-(--size-glyph) shrink-0",
          "translate-y-(--spacing-hair) items-center justify-center",
          "rounded-sm border border-edge bg-surface",
          "font-mono text-mono leading-none font-medium text-ink-soft",
          "group-data-[shortcut]/questionnaire-choice:inline-flex",
        )}
      />
    </QuestionnairePrimitive.Choice>
  );
}

/** The second line inside an answer — what the option means, under what it is called. */
export function QuestionnaireChoiceDescription({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="questionnaire-choice-description"
      className={cn("text-label text-ink-soft", className)}
      {...props}
    />
  );
}

/** A question the options cannot answer. It is the portal's one field surface, so a box the member
 *  types an answer into is the same object as every other box they type into. */
export function QuestionnaireInput({
  className,
  ...props
}: ComponentProps<typeof QuestionnairePrimitive.Input>) {
  return (
    <QuestionnairePrimitive.Input
      data-slot="questionnaire-input"
      className={cn(CONTROL, "min-h-(--size-touch) w-full min-w-0 text-ui", className)}
      {...props}
    />
  );
}

export function QuestionnaireError({
  className,
  ...props
}: ComponentProps<typeof QuestionnairePrimitive.Error>) {
  return (
    <QuestionnairePrimitive.Error
      data-slot="questionnaire-error"
      className={cn("text-label text-attention-ink", className)}
      {...props}
    />
  );
}

/** The acts, on one row: going back on the left, going on at the right, and skipping between them
 *  — so the act that moves the member forward is always in the same place whether this question is
 *  the last one or not. */
export function QuestionnaireActions({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="questionnaire-actions"
      className={cn(
        "grid min-h-(--size-touch) w-full grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-sm",
        className,
      )}
      {...props}
    />
  );
}

export function QuestionnairePrevious({
  children,
  className,
  ...props
}: ComponentProps<typeof QuestionnairePrimitive.Previous>) {
  return (
    <QuestionnairePrimitive.Previous
      data-slot="questionnaire-previous"
      className={cn(
        buttonVariants({ variant: "outline" }),
        "col-start-1 row-start-1 justify-self-start",
        className,
      )}
      {...props}
    >
      {children ?? "Back"}
    </QuestionnairePrimitive.Previous>
  );
}

export function QuestionnaireSkip({
  children,
  className,
  ...props
}: ComponentProps<typeof QuestionnairePrimitive.Skip>) {
  return (
    <QuestionnairePrimitive.Skip
      data-slot="questionnaire-skip"
      className={cn(
        buttonVariants({ variant: "outline" }),
        "col-start-2 row-start-1 justify-self-end",
        className,
      )}
      {...props}
    >
      {children ?? "Skip"}
    </QuestionnairePrimitive.Skip>
  );
}

export function QuestionnaireNext({
  children,
  className,
  ...props
}: ComponentProps<typeof QuestionnairePrimitive.Next>) {
  return (
    <QuestionnairePrimitive.Next
      data-slot="questionnaire-next"
      className={cn(
        buttonVariants({ variant: "send" }),
        "col-start-3 row-start-1 justify-self-end",
        className,
      )}
      {...props}
    >
      {children ?? "Next"}
    </QuestionnairePrimitive.Next>
  );
}

export function QuestionnaireSubmit({
  children,
  className,
  ...props
}: ComponentProps<typeof QuestionnairePrimitive.Submit>) {
  return (
    <QuestionnairePrimitive.Submit
      data-slot="questionnaire-submit"
      className={cn(
        buttonVariants({ variant: "send" }),
        "col-start-3 row-start-1 justify-self-end",
        className,
      )}
      {...props}
    >
      {children ?? "Answer"}
    </QuestionnairePrimitive.Submit>
  );
}
