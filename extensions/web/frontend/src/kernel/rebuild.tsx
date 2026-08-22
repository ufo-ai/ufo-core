import { useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { OutcomeNotice, QUIET, outcomeNotice, type NoticeState } from "@/kernel/panel";
import { postIntent } from "@/lib/api";
import { useMainAgent } from "@/lib/mainAgent";

/** Asking a job to write a page's text again.
 *
 *  The page a member is reading is drawn from rows a job wrote, and the intent this submits does
 *  not touch them: it marks the work due and the job that owns that text writes it minutes later.
 *  So the dialog exists to say what the press will and will not reach before it is pressed — a
 *  control named for the whole page while it redoes one band of it is a lie the member finds out
 *  months later — and to state what was queued afterwards, because nothing on the screen changes
 *  when they press it.
 *
 *  The outcome stays here rather than dismissing itself: it is the only account of an act with no
 *  visible result, and a refusal — the tool holds this to a workspace admin — is not something any
 *  field on the page can answer. A queued dialog has nothing left to commit, so its way out is
 *  named `Close` and the act itself is gone. */
export function RebuildDialog({
  title,
  action,
  verb,
  children,
}: {
  title: string;
  action: string;
  verb: string;
  children: ReactNode;
}) {
  const mainAgent = useMainAgent();
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const queued = Boolean(notice.text) && !notice.refused;

  async function submit() {
    if (busy || !mainAgent) return;
    setBusy(true);
    const outcome = await postIntent(mainAgent.id, { verb });
    setBusy(false);
    setNotice(outcomeNotice(outcome));
  }

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
      </DialogHeader>
      <div className="flex flex-col gap-md text-ui text-ink-soft">{children}</div>
      <OutcomeNotice state={notice} />
      <DialogFooter leave={queued ? "Close" : "Cancel"}>
        {queued ? null : (
          <Button variant="send" busy={busy} disabled={!mainAgent} onClick={submit}>
            {action}
          </Button>
        )}
      </DialogFooter>
    </DialogContent>
  );
}
