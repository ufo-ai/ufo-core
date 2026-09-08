import { cloneElement, isValidElement, type ComponentProps, type ReactElement } from "react";

import { cn } from "@/lib/cn";

export function Marker({
  className,
  render,
  ...props
}: ComponentProps<"div"> & { render?: ReactElement<{ className?: string }> }) {
  const drawn = cn(
    "relative flex min-h-(--size-glyph) w-full items-center gap-xs",
    "text-left text-label text-ink-soft",
    "[&_svg:not([class*=size-])]:size-(--size-glyph)",
    "[&_a]:underline [&_a]:underline-offset-2 [&_a]:hover:text-ink",
    className,
  );
  if (render !== undefined && isValidElement(render)) {
    return cloneElement(render, {
      ...props,
      "data-slot": "marker",
      className: cn(drawn, render.props.className),
    } as Partial<typeof render.props>);
  }
  return <div data-slot="marker" className={drawn} {...props} />;
}

export function MarkerContent({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="marker-content"
      className={cn("min-w-0 wrap-anywhere", className)}
      {...props}
    />
  );
}
