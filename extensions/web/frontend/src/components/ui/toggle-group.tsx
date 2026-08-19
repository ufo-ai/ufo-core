import * as ToggleGroupPrimitive from "@radix-ui/react-toggle-group";
import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

/** A set of options the member turns on and off independently. The group holds the whole answer as
 *  one array rather than a flag per option, so the caller reads `value` and writes it back through
 *  `onValueChange`, and `disabled` on the group settles every option inside it at once. The group
 *  lays nothing out: the caller's `className` is the grid the options fill, and the options are its
 *  direct children, because the roving focus the group carries walks its own children. */
export function ToggleGroup({
  ...props
}: Omit<ToggleGroupPrimitive.ToggleGroupMultipleProps, "type">) {
  return <ToggleGroupPrimitive.Root data-slot="toggle-group" type="multiple" {...props} />;
}

/** One option. It answers the pointer the way every act in the portal does and draws nothing else:
 *  what an option looks like is the caller's, because a group of app tiles and a row of filters
 *  press the same way and look nothing alike. It stays a `button` carrying `aria-pressed`, so what
 *  the member sees pressed is what a screen reader reads out. */
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
