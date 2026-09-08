import * as ToggleGroupPrimitive from "@radix-ui/react-toggle-group";
import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

/** Radix would read it out as a toolbar; a set of answers is a group, so the assistive layer hears one. */
export function ToggleGroup({
  ...props
}: Omit<ToggleGroupPrimitive.ToggleGroupMultipleProps, "type">) {
  return (
    <ToggleGroupPrimitive.Root data-slot="toggle-group" type="multiple" role="group" {...props} />
  );
}

export function ToggleGroupOne({
  ...props
}: Omit<ToggleGroupPrimitive.ToggleGroupSingleProps, "type">) {
  return <ToggleGroupPrimitive.Root data-slot="toggle-group-one" type="single" {...props} />;
}

export function ToggleGroupItem({
  className,
  ...props
}: ComponentProps<typeof ToggleGroupPrimitive.Item>) {
  return (
    <ToggleGroupPrimitive.Item
      data-slot="toggle-group-item"
      className={cn(
        "transition-[background-color,border-color,opacity,scale]",
        "duration-100 ease-control active:scale-[0.96]",
        "disabled:pointer-events-none disabled:opacity-(--disabled)",
        className,
      )}
      {...props}
    />
  );
}
