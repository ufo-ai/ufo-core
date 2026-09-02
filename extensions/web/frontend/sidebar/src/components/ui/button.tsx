import { cva, type VariantProps } from "class-variance-authority";
import { useState, type ComponentProps } from "react";

import { cn } from "@/lib/cn";

export const buttonVariants = cva(
  cn(
    "inline-flex items-center justify-center gap-xs",
    /* What a phone aims with is a finger, not a pointer, so every act keeps the control height as
       its floor there whatever its own text and padding come to. */
    "max-narrow:min-h-(--size-control)",
    "rounded-panel transition-[background-color,border-color,opacity,scale]",
    "duration-100 ease-control active:scale-[0.96]",
    "disabled:pointer-events-none disabled:opacity-(--disabled)",
    "[&_svg]:pointer-events-none [&_svg]:shrink-0",
  ),
  {
    variants: {
      variant: {
        send: "bg-ink text-surface font-medium px-3xl py-md border border-transparent hover:opacity-(--opacity-muted-soft)",
        outline:
          "border border-edge bg-transparent text-inherit px-lg py-xs hover:bg-fill",
        row: "border border-edge bg-transparent text-inherit rounded-control px-md py-2xs hover:bg-fill",
        quiet: "border border-transparent bg-transparent text-inherit px-lg py-xs hover:bg-fill",
        mark: "border-0 bg-transparent p-0 text-ink-soft hover:text-ink",
        option: cn(
          "border border-edge-strong bg-transparent text-inherit px-lg py-xs",
          "hover:bg-fill",
          "aria-pressed:bg-ink aria-pressed:text-surface aria-pressed:border-ink",
          "aria-pressed:hover:bg-ink aria-pressed:hover:opacity-(--opacity-muted-soft)",
        ),
      },
      size: {
        default: "",
        bar: "h-(--size-control) whitespace-nowrap rounded-full px-2xl py-0 text-label",
        /* The glyph is sized by the portal's glyph token rather than by the box's own em, so an
           icon act draws at the size of every other glyph on its line — a chevron beside a title,
           the search's lens — instead of at whatever register the surface around it happens to be
           set in. */
        icon: "size-(--size-control) rounded-full p-0 [&_svg]:size-(--size-glyph)",
        /* The mark's own box: the glyph and nothing around it, so a row of acts is a row of marks
           at the glyph's own pitch rather than a row of boxes with a glyph inside each. */
        glyph: "size-(--size-glyph) rounded-control p-0 [&_svg]:size-(--size-glyph)",
      },
    },
    defaultVariants: { variant: "outline", size: "default" },
  },
);

export type ButtonProps = ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & { busy?: boolean };

/** A button lays its content out as a centred row, so a glyph sits in the middle of the box rather
 *  than on the text baseline at its left edge, and `size="icon"` is the box a glyph alone is drawn
 *  in: one `--size-control` circle, which is the shape every icon-only act in the portal takes.
 *  `size="bar"` is the same height drawn as a pill: the acts standing in a page's header or a
 *  section's bar are the height and the shape of the search and the filter beside them, so a band
 *  of controls reads as one row rather than as a tall act with chrome tucked under it.
 *
 *  `busy` marks an act already in flight. The button keeps its place in the accessibility tree —
 *  `disabled` would drop the focused element out of it mid-submit — and swallows the activation
 *  instead, so a second click cannot commit the act twice or submit the form it sits in. */
export function Button({
  className,
  variant,
  size,
  type = "button",
  busy,
  onClick,
  ...props
}: ButtonProps) {
  return (
    <button
      data-slot="button"
      type={type}
      aria-disabled={busy ? true : undefined}
      onClick={(event) => {
        if (busy) {
          event.preventDefault();
          return;
        }
        onClick?.(event);
      }}
      className={cn(
        buttonVariants({ variant, size }),
        busy && "opacity-(--opacity-muted) animate-working motion-reduce:animate-none",
        className,
      )}
      {...props}
    />
  );
}

/** A destructive act: the first click arms the button and names the act it will commit, the
 *  second click commits it. Leaving the button disarms it. */
export function ConfirmButton({ verb, onClick, className, ...props }: ButtonProps & { verb: string }) {
  const [armed, setArmed] = useState(false);
  return (
    <Button
      {...props}
      className={cn(armed && "font-strong", className)}
      onBlur={() => setArmed(false)}
      onClick={(event) => {
        if (!armed) {
          setArmed(true);
          return;
        }
        setArmed(false);
        onClick?.(event);
      }}
    >
      {armed ? "Confirm " + verb.toLowerCase() : verb}
    </Button>
  );
}
