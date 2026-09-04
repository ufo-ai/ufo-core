import type { ComponentProps } from "react";

import { IconChevronDown, IconChevronUp, IconCornerDownLeft } from "@tabler/icons-react";
import { Questionnaire as QuestionnairePrimitive } from "@shadcn/react/questionnaire";

import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/cn";

export type { QuestionnaireItemDefinition, QuestionnaireItemStatus } from "@shadcn/react/questionnaire";

/** One answer, drawn as the whole row rather than as a mark with a label beside it: the member
 *  presses anywhere in it, or types anywhere in it. `min-w-0` is what keeps the row inside the
 *  card that holds it: a grid item's automatic minimum is its content, and a description that
 *  never wraps would otherwise carry every row past the card's own edge. */
export const ROW = cn(
  "relative flex min-h-(--size-touch) min-w-0 items-center gap-md rounded-panel border border-edge",
  "bg-transparent px-lg py-sm text-start text-ui transition-colors hover:bg-fill",
);

/** The key a row is answered by, at its leading edge: the letter the run gives each answer in
 *  order, which is also the key that presses it. */
export const KEY = cn(
  "pointer-events-none flex size-(--size-avatar) shrink-0 items-center justify-center",
  "rounded-control border border-edge bg-fill",
  "font-mono text-mono leading-none font-medium text-ink-soft",
);

/** One chevron of the stepper: a glyph in the control's own square, with no border of its own, so
 *  the pair reads as one count rather than as two buttons standing beside a number. */
const STEP = cn(
  "flex size-(--size-control) items-center justify-center rounded-control",
  "text-ink-soft transition-colors duration-100 ease-control hover:bg-fill hover:text-ink",
);

/** The act that takes the member on, and the key that takes them on without the pointer. */
const ONWARD = (
  <>
    Continue
    <IconCornerDownLeft aria-hidden className="size-icon" />
  </>
);

/** What a turn asks of the member, taken one question at a time. It is a real form: every answer
 *  is a native control with a native name, so what the member chose survives a reload, reads back
 *  to assistive technology as the radio or checkbox it is, and arrives as form data rather than as
 *  state some component was holding.
 *
 *  Asking one at a time is the point. A turn may ask up to four things, and four stacked questions
 *  in a transcript is a wall the member has to parse before answering any of it; a step names one
 *  decision, says where it sits in the run, and lets them go back.
 *
 *  Every answer carries a letter, so the run is answered from the keyboard as readily as from the
 *  pointer and the key the member presses is drawn on the row it presses. */
export function Questionnaire({
  className,
  ...props
}: Omit<ComponentProps<typeof QuestionnairePrimitive.Root>, "shortcuts">) {
  return (
    <QuestionnairePrimitive.Root
      data-slot="questionnaire"
      className={cn("flex w-full min-w-0 flex-col gap-2xl", className)}
      {...props}
      shortcuts="letters"
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

/** The question itself, set larger than the answers under it: the member reads the decision first
 *  and the options as what answers it. */
export function QuestionnaireTitle({
  className,
  ...props
}: ComponentProps<typeof QuestionnairePrimitive.Title>) {
  return (
    <QuestionnairePrimitive.Title
      data-slot="questionnaire-title"
      className={cn(
        "text-subtitle leading-chrome text-pretty",
        "mb-2xl",
        className,
      )}
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

/** One answer the member presses. The native control is laid over the row invisibly and still
 *  carries the focus, the name and the value, so nothing here reimplements what a radio already
 *  is; the key states which answer it is, and the row states that it is the chosen one.
 *
 *  The key sits on the line naming the answer rather than in the middle of however many lines
 *  explain it, so the row's own inset carries its height in place of the touch floor — which is
 *  what keeps a one-line answer reading as centred inside it. */
export function QuestionnaireChoice({
  children,
  className,
  ...props
}: ComponentProps<typeof QuestionnairePrimitive.Choice>) {
  return (
    <QuestionnairePrimitive.Choice
      data-slot="questionnaire-choice"
      className={cn(
        ROW,
        "items-start py-lg",
        "group/questionnaire-choice cursor-pointer outline-none select-none",
        "has-[>input:focus-visible]:border-edge-strong",
        "data-checked:border-edge-strong data-checked:bg-fill",
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
      <QuestionnairePrimitive.ChoiceShortcut
        data-slot="questionnaire-choice-shortcut"
        className={cn(
          KEY,
          "group-data-checked/questionnaire-choice:border-ink",
          "group-data-checked/questionnaire-choice:bg-ink",
          "group-data-checked/questionnaire-choice:text-surface",
        )}
      />
      <QuestionnairePrimitive.ChoiceLabel
        data-slot="questionnaire-choice-label"
        className="flex min-w-0 flex-1 flex-col gap-2xs leading-chrome"
      >
        {children}
      </QuestionnairePrimitive.ChoiceLabel>
    </QuestionnairePrimitive.Choice>
  );
}

/** What the option means, under what it is called. It wraps rather than truncating — a sentence
 *  cut at the row's edge loses the clause that distinguishes it from the option above — and holds
 *  to two lines, so a column of answers stays a column the member can read down. The answer they
 *  chose states itself whole: it is the one they are acting on, and its length no longer costs
 *  them the ones they are comparing it against. */
export function QuestionnaireChoiceDescription({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="questionnaire-choice-description"
      className={cn(
        "min-w-0 line-clamp-2 text-label wrap-anywhere text-ink-soft",
        "group-data-checked/questionnaire-choice:line-clamp-none",
        className,
      )}
      {...props}
    />
  );
}

/** The answer the options do not hold, drawn as the last of them: a row with its own key that the
 *  member types into. Words here are an answer like any other — filling the row settles the
 *  question the way pressing a choice does, and the row states that as the choices state it.
 *
 *  The name the row carries once it holds words is the whole of what submits it, so the `form=""`
 *  the primitive also hangs on an empty row never reaches the DOM: Gecko does not return an element
 *  to its ancestor form when that attribute is removed, so the words the member typed would sit in
 *  no form at all and Continue would submit an empty answer they never see refused. */
export function QuestionnaireInput({
  shortcut,
  className,
  ...props
}: ComponentProps<typeof QuestionnairePrimitive.Input> & { shortcut: string }) {
  return (
    <label
      data-slot="questionnaire-write"
      className={cn(
        ROW,
        "group/questionnaire-write items-center",
        "has-[input:focus-visible]:border-edge-strong",
        "has-[input[data-filled]]:border-primary has-[input[data-filled]]:bg-fill",
      )}
    >
      <span
        aria-hidden
        data-slot="questionnaire-write-shortcut"
        className={cn(
          KEY,
          "group-has-[input[data-filled]]/questionnaire-write:border-primary",
          "group-has-[input[data-filled]]/questionnaire-write:bg-primary",
          "group-has-[input[data-filled]]/questionnaire-write:text-primary-foreground",
        )}
      >
        {shortcut}
      </span>
      <QuestionnairePrimitive.Input
        data-slot="questionnaire-input"
        className={cn(
          "min-w-0 flex-1 bg-transparent text-ui outline-none placeholder:text-ink-faint",
          className,
        )}
        {...props}
        render={(rendered) => <input {...rendered} form={undefined} />}
      />
    </label>
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

/** The acts, on one row: where the member is in the run on the left, and what takes them out of
 *  this question on the right — so the act that moves them forward is always in the same place
 *  whether this question is the last one or not. */
export function QuestionnaireActions({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="questionnaire-actions"
      className={cn(
        "flex min-h-(--size-touch) w-full items-center justify-end gap-sm",
        className,
      )}
      {...props}
    />
  );
}

/** Where the member is in the run, and the two acts that move them through it. The count sits
 *  between the chevrons in fixed tracks, so the digits hold one place whether or not there is a
 *  question either side of this one. */
export function QuestionnaireStepper({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="questionnaire-stepper"
      className={cn(
        "me-auto grid grid-cols-[var(--size-control)_auto_var(--size-control)] items-center",
        className,
      )}
      {...props}
    >
      <QuestionnairePrimitive.Previous
        data-slot="questionnaire-previous"
        aria-label="Back"
        className={cn(STEP, "col-start-1")}
      >
        <IconChevronUp aria-hidden className="size-icon" />
      </QuestionnairePrimitive.Previous>
      <QuestionnairePrimitive.Progress
        data-slot="questionnaire-progress"
        className="col-start-2 text-center font-mono text-mono font-medium tabular-nums text-ink-soft"
        render={(rendered, state) => (
          <div {...rendered}>{state.current + "/" + state.total}</div>
        )}
      />
      <QuestionnairePrimitive.Next
        data-slot="questionnaire-next"
        aria-label="Next"
        className={cn(STEP, "col-start-3")}
      >
        <IconChevronDown aria-hidden className="size-icon" />
      </QuestionnairePrimitive.Next>
    </div>
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
      className={cn(buttonVariants({ variant: "outline", size: "bar" }), className)}
      {...props}
    >
      {children ?? "Skip"}
    </QuestionnairePrimitive.Skip>
  );
}

export function QuestionnaireOnward({
  children,
  className,
  ...props
}: ComponentProps<typeof QuestionnairePrimitive.Next>) {
  return (
    <QuestionnairePrimitive.Next
      data-slot="questionnaire-onward"
      className={cn(buttonVariants({ variant: "send", size: "bar" }), className)}
      {...props}
    >
      {children ?? ONWARD}
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
      className={cn(buttonVariants({ variant: "send", size: "bar" }), className)}
      {...props}
    >
      {children ?? ONWARD}
    </QuestionnairePrimitive.Submit>
  );
}
