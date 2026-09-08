import type { ComponentProps } from "react";

import { IconChevronDown, IconChevronUp, IconCornerDownLeft } from "@tabler/icons-react";
import { Questionnaire as QuestionnairePrimitive } from "@shadcn/react/questionnaire";

import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/cn";

export type { QuestionnaireItemDefinition, QuestionnaireItemStatus } from "@shadcn/react/questionnaire";

export const ROW = cn(
  "relative flex min-h-(--size-touch) min-w-0 items-center gap-md rounded-panel border border-edge",
  "bg-transparent px-lg py-sm text-start text-ui transition-colors hover:bg-fill",
);

export const KEY = cn(
  "pointer-events-none flex size-(--size-avatar) shrink-0 items-center justify-center",
  "rounded-control border border-edge bg-fill",
  "font-mono text-mono leading-none font-medium text-ink-soft",
);

const STEP = cn(
  "flex size-(--size-control) items-center justify-center rounded-control",
  "text-ink-soft transition-colors duration-100 ease-control hover:bg-fill hover:text-ink",
);

const ONWARD = (
  <>
    Continue
    <IconCornerDownLeft aria-hidden className="size-icon" />
  </>
);

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

/** Gecko does not return an element to its ancestor form when a `form` attribute is removed, so the
 *  `form=""` the primitive hangs on an empty row must never reach the DOM. */
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
