import * as LabelPrimitive from "@radix-ui/react-label";
import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

const CONTROL =
  "rounded-panel border border-edge-control bg-field text-field-ink px-lg py-md font-inherit";

export function Label({ className, ...props }: ComponentProps<typeof LabelPrimitive.Root>) {
  return (
    <LabelPrimitive.Root
      className={cn("block font-strong mb-hair", className)}
      {...props}
    />
  );
}

export function Input({ className, type = "text", ...props }: ComponentProps<"input">) {
  return <input type={type} className={cn(CONTROL, "w-full max-w-control", className)} {...props} />;
}

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return (
    <textarea
      className={cn(CONTROL, "w-full max-w-section min-h-[12em] font-mono text-mono", className)}
      {...props}
    />
  );
}

export function Select({ className, ...props }: ComponentProps<"select">) {
  return <select className={cn(CONTROL, "max-w-control", className)} {...props} />;
}

export function Checkbox({ className, ...props }: ComponentProps<"input">) {
  return <input type="checkbox" className={cn("align-middle", className)} {...props} />;
}

export function Hint({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn("text-label opacity-(--muted-faint) max-w-hint mt-2xs mb-lg", className)}
      {...props}
    />
  );
}
