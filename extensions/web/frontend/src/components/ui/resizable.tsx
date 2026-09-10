import { IconGripVertical } from "@tabler/icons-react";
import * as ResizablePrimitive from "react-resizable-panels";

import { cn } from "@/lib/cn";

export function ResizablePanelGroup({ className, ...props }: ResizablePrimitive.GroupProps) {
  return (
    <ResizablePrimitive.Group
      data-slot="resizable-panel-group"
      className={cn("flex size-full aria-[orientation=vertical]:flex-col", className)}
      {...props}
    />
  );
}

export function ResizablePanel(props: ResizablePrimitive.PanelProps) {
  return <ResizablePrimitive.Panel data-slot="resizable-panel" {...props} />;
}

export function ResizableHandle({
  withHandle,
  className,
  ...props
}: ResizablePrimitive.SeparatorProps & { withHandle?: boolean }) {
  return (
    <ResizablePrimitive.Separator
      data-slot="resizable-handle"
      className={cn(
        "relative flex w-px items-center justify-center bg-edge",
        "after:absolute after:inset-y-0 after:left-1/2 after:w-1 after:-translate-x-1/2",
        className,
      )}
      {...props}
    >
      {withHandle ? (
        <div className="z-10 flex h-4 w-3 items-center justify-center rounded-xs border border-edge bg-fill">
          <IconGripVertical className="size-2.5" aria-hidden />
        </div>
      ) : null}
    </ResizablePrimitive.Separator>
  );
}
