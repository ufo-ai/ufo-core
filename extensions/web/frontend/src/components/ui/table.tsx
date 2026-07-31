import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

export function Table({ className, ...props }: ComponentProps<"table">) {
  return (
    <div className="mb-4xl overflow-x-auto">
      <table className={cn("w-full border-collapse", className)} {...props} />
    </div>
  );
}

export function Th({ className, ...props }: ComponentProps<"th">) {
  return (
    <th
      className={cn(
        "text-left align-baseline px-md py-xs border-b border-edge-soft",
        "text-small font-strong opacity-(--muted)",
        className,
      )}
      {...props}
    />
  );
}

export function Td({ className, ...props }: ComponentProps<"td">) {
  return (
    <td
      className={cn("text-left align-baseline px-md py-xs border-b border-edge-soft", className)}
      {...props}
    />
  );
}
