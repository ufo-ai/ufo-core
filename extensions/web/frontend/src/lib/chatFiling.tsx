import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { postObjectAction } from "@/lib/api";
import { useViewer } from "@/lib/audience";
import {
  ARCHIVE_ACTION,
  CONVERSATION_KIND,
  DELETE_ACTION,
  FILING_MARKS,
  PIN_ACTION,
  UNARCHIVE_ACTION,
  UNPIN_ACTION,
} from "@/lib/rail";
import { freshenRail, railFiled } from "@/lib/railStore";
import type { Conversation } from "@/lib/types";

/** What a row's act filed, so the listing that drew the row answers for it before the reads do. */
export type Filed = (row: Conversation, action: string) => void;

export type ChatFiling = {
  busy: boolean;
  refused: string;
  asking: boolean;
  owned: boolean;
  setAsking: (asking: boolean) => void;
  file: (action: string) => Promise<void>;
};

/** The act posts its verb, the marks it set are drawn at once on every surface holding the row, and
 *  the reads behind them confirm it. `owned` answers whether delete is this member's to offer,
 *  because the verb refuses anybody else; a control that refuses on press is a control that should
 *  not have been drawn. */
export function useChatFiling(row: Conversation, onFiled?: Filed): ChatFiling {
  const acting = useViewer();
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [refused, setRefused] = useState("");
  const owned =
    acting !== null && (row.owner_email === acting || (row.mine && row.owner_email === null));
  const file = async (action: string) => {
    setBusy(true);
    setRefused("");
    const outcome = await postObjectAction(
      row.agent_id,
      { kind: CONVERSATION_KIND, name: row.conversation_id, action },
      {},
    );
    setBusy(false);
    if (!outcome.applied) {
      setRefused(outcome.message);
      return;
    }
    setAsking(false);
    railFiled({ ...row, ...FILING_MARKS[action] });
    void freshenRail();
    onFiled?.(row, action);
  };
  return { busy, refused, asking, owned, setAsking, file };
}

/** The filing acts a conversation carries wherever its menu is drawn. */
export function ChatFilingItems({ row, filing }: { row: Conversation; filing: ChatFiling }) {
  return (
    <>
      <DropdownMenuItem
        onSelect={() => void filing.file(row.archived ? UNARCHIVE_ACTION : ARCHIVE_ACTION)}
      >
        {row.archived ? "Unarchive" : "Archive"}
      </DropdownMenuItem>
      <DropdownMenuItem onSelect={() => void filing.file(row.pinned ? UNPIN_ACTION : PIN_ACTION)}>
        {row.pinned ? "Unpin" : "Pin"}
      </DropdownMenuItem>
      {filing.owned ? (
        <DropdownMenuItem onSelect={() => filing.setAsking(true)}>Delete</DropdownMenuItem>
      ) : null}
    </>
  );
}

/** What delete asks before it runs, and what any refused act answered. */
export function ChatFilingDialog({ row, filing }: { row: Conversation; filing: ChatFiling }) {
  return (
    <>
      {filing.refused && !filing.asking ? (
        <span role="status" className="text-label text-ink-soft">
          {filing.refused}
        </span>
      ) : null}
      <Dialog open={filing.asking} onOpenChange={filing.setAsking}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this conversation?</DialogTitle>
            <DialogDescription>
              {row.title} leaves every listing and only you, its owner, can restore it.
            </DialogDescription>
          </DialogHeader>
          {filing.refused ? (
            <p role="status" className="m-0 text-small text-ink-soft">
              {filing.refused}
            </p>
          ) : null}
          <DialogFooter>
            <Button busy={filing.busy} onClick={() => void filing.file(DELETE_ACTION)}>
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
