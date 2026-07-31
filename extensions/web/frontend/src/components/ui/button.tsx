import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

const button = cva("rounded-panel disabled:cursor-default", {
  variants: {
    variant: {
      send: "bg-ink text-surface font-strong px-3xl py-md border-0 disabled:opacity-(--disabled)",
      outline: "border border-edge-control bg-transparent text-inherit px-lg py-xs",
      row: "border border-edge-control bg-transparent text-inherit rounded-control px-sm py-hair mr-xs",
      option:
        "border border-edge-control-strong bg-transparent text-inherit px-lg py-xs disabled:opacity-(--disabled)",
    },
  },
  defaultVariants: { variant: "outline" },
});

export type ButtonProps = ComponentProps<"button"> & VariantProps<typeof button>;

export function Button({ className, variant, type = "button", ...props }: ButtonProps) {
  return <button type={type} className={cn(button({ variant }), className)} {...props} />;
}
