import * as LabelPrimitive from "@radix-ui/react-label";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/cn";

/** The bordered field surface. `Select` draws its trigger with this too, so a button that stands in
 *  for a field is the same object to the eye as the fields beside it. An answer beneath filled
 *  choices uses their surface so the choices and field read as one column. */
export const CONTROL = cn(
  "rounded-panel border border-edge bg-field text-field-ink px-lg py-md font-sans",
  "text-subtitle narrow:text-ui placeholder:text-ink-faint",
  "transition-[border-color] duration-100 ease-control hover:border-edge-strong",
  "user-invalid:border-ink user-invalid:border-dashed",
  "disabled:cursor-not-allowed disabled:opacity-(--disabled) disabled:hover:border-edge",
);

export function Label({ className, ...props }: ComponentProps<typeof LabelPrimitive.Root>) {
  return <LabelPrimitive.Root data-slot="label" className={cn("block font-strong", className)} {...props} />;
}

/** One labelled control. The label sits over the control it names, never beside it: a settings
 *  sheet holds names as long as `Internet Access Allowed` beside boxes as wide as a model id, and
 *  a label column sized for both is a column of whitespace on every other row. A checkbox is the
 *  exception the form draws itself — the box is the width of a glyph, so its label reads across. */
export function Field({
  label,
  htmlFor,
  description,
  children,
}: {
  label: ReactNode;
  htmlFor: string;
  description?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div data-slot="field" className="flex flex-col gap-2xs">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {description ? (
        <Hint id={htmlFor + "-description"} className="m-0">
          {description}
        </Hint>
      ) : null}
    </div>
  );
}

/** A form is a card the way a table is a card, and the act that commits it sits on a footer row
 *  under the card's own rule, set right where the last field ends. A submit floating loose beneath
 *  the final input belongs to nothing on the page and reads as the opening of whatever follows. */
export function FieldGroup({
  submit,
  className,
  children,
  ...props
}: Omit<ComponentProps<"form">, "action"> & { submit: ReactNode }) {
  return (
    <form data-slot="field-group" className={cn("rounded-panel border border-edge bg-card text-card-foreground", className)} {...props}>
      <div className="flex flex-col gap-xl p-xl">{children}</div>
      <div className="flex justify-end border-t border-edge px-xl py-lg">{submit}</div>
    </form>
  );
}

const INPUT_SURFACES = {
  control: cn(CONTROL, "w-full max-w-control"),
  answer: cn(
    "h-10 w-full rounded-(--radius-answer) border border-transparent bg-fill px-2xl py-0",
    "font-sans text-label text-field-ink placeholder:text-ink-soft",
    "transition-[background-color] duration-100 ease-control hover:bg-fill-strong",
    "focus-visible:bg-fill-strong focus-visible:outline-none",
    "user-invalid:border-ink user-invalid:border-dashed",
    "disabled:cursor-not-allowed disabled:opacity-(--disabled) disabled:hover:bg-fill",
  ),
} as const;

export function Input({
  className,
  surface = "control",
  type = "text",
  ...props
}: ComponentProps<"input"> & { surface?: keyof typeof INPUT_SURFACES }) {
  return (
    <input
      data-slot="input"
      type={type}
      className={cn(INPUT_SURFACES[surface], className)}
      {...props}
    />
  );
}

/** The one search box, in the bar that narrows a listing. It is a filled pill and not the bordered
 *  field surface: the bar's controls sit against the records they act on, and a filled box beside
 *  the filter's filled tab reads as one band belonging to the table, where a bordered field the
 *  height of a form control reads as the page asking a question. The glyph is drawn here on
 *  `currentColor`, as the chevron and the tick are. Enter submits where the read is the server's —
 *  the box is the control, and a button beside it would say the word again; a listing that narrows
 *  what it already holds passes no `onSubmit` and narrows on every keystroke. */
export function Search({
  label,
  onSubmit,
  className,
  ...props
}: Omit<ComponentProps<"input">, "type"> & {
  label: string;
  onSubmit?: () => void;
}) {
  return (
    <form
      className={cn(
        "flex h-(--size-control) w-(--container-search) items-center gap-sm rounded-full",
        "bg-fill px-lg",
        "focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-ink",
        className,
      )}
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit?.();
      }}
    >
      <svg
        viewBox="0 0 16 16"
        aria-hidden
        className="size-(--size-glyph) shrink-0 text-ink-soft"
      >
        <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
        <path d="M10.5 10.5L14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
      <input
        type="search"
        aria-label={label}
        /* The box stands the height of the pill it sits in rather than the height of its own
           line: the pill is what the member aims at, and a finger landing on it must reach the
           field. */
        className={cn(
          "min-w-0 flex-1 self-stretch border-0 bg-transparent p-0 font-sans text-label",
          "placeholder:text-ink-faint focus-visible:outline-none",
        )}
        {...props}
      />
    </form>
  );
}

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        CONTROL,
        "w-full max-w-section min-h-[var(--size-textarea)] text-subtitle narrow:text-ui",
        className,
      )}
      {...props}
    />
  );
}

const GROWING_CELL = "col-start-1 row-start-1 w-full";

/** The box inside a card that is itself the field: it draws no surface of its own, because a second
 *  border and fill inside the first states a box within a box. The mirror takes the same string, so
 *  the row it sizes is the row the member is typing into. */
const BARE = cn(
  "border-0 bg-transparent p-0 text-field-ink font-sans",
  "text-label placeholder:text-ink-faint",
);

/** A textarea exactly as tall as what is in it, to a fold. The value is drawn twice — once in a
 *  mirror that sizes the row, once in the textarea laid over it — so the box grows on the
 *  browser's own layout pass, in the same paint as the keystroke. Measuring `scrollHeight` and
 *  writing a height back is a second answer to how tall the box is, and it arrives a paint late.
 *  The mirror stops at `--size-composer`, so past the fold the row holds and the textarea scrolls.
 *  The trailing space is what gives a final newline a line of its own, since a line box ends at
 *  the break otherwise. */
export function GrowingTextarea({
  className,
  value,
  bare = false,
  ...props
}: Omit<ComponentProps<"textarea">, "value" | "rows"> & { value: string; bare?: boolean }) {
  const surface = bare ? BARE : CONTROL;
  return (
    <div className={cn("grid", className)}>
      <div
        aria-hidden
        className={cn(
          surface,
          GROWING_CELL,
          "invisible max-h-(--size-composer) overflow-hidden whitespace-pre-wrap wrap-anywhere",
        )}
      >
        {value + " "}
      </div>
      <textarea
        data-slot="growing-textarea"
        rows={1}
        value={value}
        className={cn(surface, GROWING_CELL, "resize-none overflow-y-auto")}
        {...props}
      />
    </div>
  );
}

export function Checkbox({ className, ...props }: ComponentProps<"input">) {
  return (
    <input
      data-slot="checkbox"
      type="checkbox"
      className={cn(
        "size-(--spacing-2xl) accent-ink align-middle",
        "transition-[scale] duration-100 ease-control active:scale-[0.96]",
        "disabled:cursor-not-allowed disabled:opacity-(--disabled)",
        className,
      )}
      {...props}
    />
  );
}

/** A setting a member turns on and off, drawn as the track it slides in. It is a checkbox
 *  underneath — the browser's own control, so it is reached by keyboard, announced as a checkbox,
 *  and submitted with the form it stands in — wearing a track and a thumb instead of a tick. The
 *  state is read from the box itself with `checked:`, so nothing has to be told twice. */
export function Switch({ className, ...props }: ComponentProps<"input">) {
  return (
    <input
      data-slot="switch"
      type="checkbox"
      role="switch"
      className={cn(
        "relative h-(--size-switch) w-(--size-switch-track) shrink-0 cursor-pointer appearance-none",
        "rounded-full bg-fill-strong transition-colors duration-100 ease-control",
        "checked:bg-ink",
        "before:absolute before:top-1/2 before:left-(--spacing-hair) before:size-(--size-switch-thumb)",
        "before:-translate-y-1/2 before:rounded-full before:bg-surface before:content-['']",
        "before:transition-[translate] before:duration-100 before:ease-control",
        "checked:before:translate-x-(--size-switch-throw)",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink",
        "disabled:cursor-not-allowed disabled:opacity-(--disabled)",
        className,
      )}
      {...props}
    />
  );
}

export function Hint({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="field-description"
      className={cn("text-label text-ink-soft max-w-hint mt-2xs mb-lg", className)}
      {...props}
    />
  );
}
