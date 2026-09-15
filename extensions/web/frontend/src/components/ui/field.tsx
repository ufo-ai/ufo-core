import * as LabelPrimitive from "@radix-ui/react-label";
import { IconX } from "@tabler/icons-react";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/cn";

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

export const CLEAR_SEARCH = "Clear search";

/** `onClear` is what empties the field and the read behind it in one press, so a member is never
 *  left deleting a query by hand to see everything again. Escape clears it from the keys, which is
 *  what the native `type=search` control does in the browsers that draw one. */
export function Search({
  label,
  onSubmit,
  onClear,
  className,
  ...props
}: Omit<ComponentProps<"input">, "type"> & {
  label: string;
  onSubmit?: () => void;
  onClear?: () => void;
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
        onKeyDown={(event) => {
          if (event.key === "Escape" && onClear) onClear();
        }}
        className={cn(
          "min-w-0 flex-1 self-stretch border-0 bg-transparent p-0 font-sans text-label",
          "placeholder:text-ink-faint focus-visible:outline-none",
          "[&::-webkit-search-cancel-button]:hidden",
        )}
        {...props}
      />
      {onClear && props.value ? (
        <button
          type="button"
          aria-label={CLEAR_SEARCH}
          onClick={onClear}
          className={cn(
            "flex shrink-0 items-center justify-center rounded-full border-0 bg-transparent p-0",
            "text-ink-soft hover:text-ink",
          )}
        >
          <IconX className="size-(--size-glyph) shrink-0" aria-hidden />
        </button>
      ) : null}
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

const BARE = cn(
  "border-0 bg-transparent p-0 text-field-ink font-sans",
  "text-label placeholder:text-ink-faint",
);

/** The value is drawn twice, so the box grows on the browser's own layout pass: measuring `scrollHeight`
 *  and writing a height back arrives a paint late. The trailing space gives a final newline its line. */
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
        data-slot="growing-mirror"
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
