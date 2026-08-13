import * as LabelPrimitive from "@radix-ui/react-label";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/cn";

/** The one field surface. `Select` draws its trigger with this too, so a button that stands in for
 *  a field is the same object to the eye as the fields beside it. */
export const CONTROL = cn(
  "rounded-panel border border-edge-control bg-field text-field-ink px-lg py-md font-inherit",
  "text-subtitle narrow:text-ui placeholder:opacity-(--muted)",
  "transition-[border-color] duration-100 ease-control hover:border-edge-control-strong",
  "user-invalid:border-ink user-invalid:border-dashed",
  "disabled:cursor-not-allowed disabled:opacity-(--disabled) disabled:hover:border-edge-control",
);

export function Label({ className, ...props }: ComponentProps<typeof LabelPrimitive.Root>) {
  return <LabelPrimitive.Root className={cn("block font-strong", className)} {...props} />;
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
    <div className="flex flex-col gap-2xs">
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
    <form className={cn("rounded-panel border border-edge bg-surface", className)} {...props}>
      <div className="flex flex-col gap-xl p-xl">{children}</div>
      <div className="flex justify-end border-t border-edge-soft px-xl py-lg">{submit}</div>
    </form>
  );
}

export function Input({ className, type = "text", ...props }: ComponentProps<"input">) {
  return <input type={type} className={cn(CONTROL, "w-full max-w-control", className)} {...props} />;
}

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return (
    <textarea
      className={cn(
        CONTROL,
        "w-full max-w-section min-h-[var(--size-textarea)] font-mono text-subtitle narrow:text-mono",
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
  "border-0 bg-transparent p-0 text-field-ink font-inherit",
  "text-subtitle narrow:text-ui placeholder:opacity-(--muted)",
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

export function Hint({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn("text-label opacity-(--muted-faint) max-w-hint mt-2xs mb-lg", className)}
      {...props}
    />
  );
}
