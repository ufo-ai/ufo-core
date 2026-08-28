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

/** Asking a job to write a page's text again.
 *
 *  The page a member is reading is drawn from rows a job wrote, and the act this offers does not
 *  touch them: it marks the work due and the job that owns that text writes it minutes later. So
 *  the dialog exists to say what the press will and will not reach before it is pressed — a control
 *  named for the whole page while it redoes one band of it is a lie the member finds out months
 *  later — and to state what was queued afterwards, because nothing on the screen changes when they
 *  press it.
 *
 *  The acts are the ones `kind`'s collection projects from its declarations — the rebuild — posted
 *  on the main agent's lane, so the page carries no rule about who may press it or what the rebuild
 *  reaches. The outcome stays here rather than dismissing itself: it is the only account of an act
 *  with no visible result, and a refusal — the action holds this to a workspace admin — is not
 *  something any field on the page can answer. A queued dialog has nothing left to commit, so its
 *  way out is named `Close` and the act itself is gone. */
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
