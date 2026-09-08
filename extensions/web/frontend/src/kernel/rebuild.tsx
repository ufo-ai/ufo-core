import { useState, type ReactNode } from "react";

import {
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ActionForm } from "@/kernel/action";
import { Notice, Panel, usePanelRead } from "@/kernel/panel";
import { postAction } from "@/lib/api";
import { useMainAgent } from "@/lib/mainAgent";
import type { ActionView } from "@/lib/types";

/** The press does not touch the rows the page is drawn from: it marks the work due, and the job that
 *  owns that text writes it minutes later. */
export function RebuildDialog({
  title,
  kind,
  children,
}: {
  title: string;
  kind: string;
  children: ReactNode;
}) {
  const mainAgent = useMainAgent();
  const [queued, setQueued] = useState<string | null>(null);
  const acts = usePanelRead<{ actions: ActionView[] }>("/actions/" + kind);

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
      </DialogHeader>
      <div className="flex flex-col gap-md text-ui text-ink-soft">{children}</div>
      {queued === null ? (
        <Panel state={acts} loading={() => null} failed={(message) => <Notice>{message}</Notice>}>
          {({ actions }) =>
            actions.map((view) => (
              <ActionForm
                key={view.name}
                view={view}
                act={async (input) => {
                  if (!mainAgent) return { applied: false, message: "" };
                  const outcome = await postAction(mainAgent.id, view.call, input);
                  if (outcome.applied) setQueued(outcome.message);
                  return outcome;
                }}
              />
            ))
          }
        </Panel>
      ) : (
        <Notice>{queued}</Notice>
      )}
      <DialogFooter leave={queued === null ? "Cancel" : "Close"} />
    </DialogContent>
  );
}
