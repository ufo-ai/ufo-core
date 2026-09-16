import { cva, type VariantProps } from "class-variance-authority";
import { cloneElement, isValidElement, type ComponentProps, type ReactElement } from "react";

import { cn } from "@/lib/cn";

const markerVariants = cva(
  cn(
    "relative flex min-h-(--size-glyph) w-full items-center gap-xs",
    "text-left text-ink-soft",
    "[&_svg:not([class*=size-])]:size-(--size-glyph)",
    "[&_a]:underline [&_a]:underline-offset-2 [&_a]:hover:text-ink",
  ),
  {
    variants: {
      variant: {
        default: "text-label",
        stamp: "text-small leading-none tabular-nums",
      },
      align: { start: "", end: "justify-end text-right" },
      indent: { true: "pl-2xl", false: "" },
      reveal: {
        true: cn(
          "opacity-0 transition-opacity motion-reduce:transition-none",
          "group-hover/message:opacity-100 group-focus-within/message:opacity-100",
        ),
        false: "",
      },
    },
    defaultVariants: { variant: "default", align: "start", indent: false, reveal: false },
  },
);

/** `reveal` holds the marker off the message until a pointer or keyboard focus reaches it, so it
 *  needs a `group/message` ancestor to hover against. Tailwind reads this prose for class names, so
 *  no word in it may also be a bare utility: one such word emits `--tw-ring-offset-color:#fff`, and
 *  `theme.test.tsx` then refuses the built sheet for painting outside the palette. */
export function Marker({
  className,
  variant,
  align,
  indent,
  reveal,
  render,
  ...props
}: ComponentProps<"div"> &
  VariantProps<typeof markerVariants> & { render?: ReactElement<{ className?: string }> }) {
  const drawn = cn(markerVariants({ variant, align, indent, reveal }), className);
  if (render !== undefined && isValidElement(render)) {
    return cloneElement(render, {
      ...props,
      "data-slot": "marker",
      className: cn(drawn, render.props.className),
    } as Partial<typeof render.props>);
  }
  return <div data-slot="marker" className={drawn} {...props} />;
}

export function MarkerContent({
  className,
  working = false,
  truncate = false,
  ...props
}: ComponentProps<"span"> & { working?: boolean; truncate?: boolean }) {
  return (
    <span
      data-slot="marker-content"
      className={cn("min-w-0 wrap-anywhere", working && "shimmer", truncate && "truncate", className)}
      {...props}
    />
  );
}
