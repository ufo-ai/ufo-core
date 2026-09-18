import { useState } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Facts, Group } from "@/components/ui/facts";
import { OutcomeNotice, Panel, QUIET, outcomeNotice, usePanelRead, type NoticeState } from "@/kernel/panel";
import { postAction } from "@/lib/api";
import { useMainAgent } from "@/lib/mainAgent";
import type { ExportView } from "@/lib/contract";
import type { ActionView } from "@/lib/types";

export function WorkspaceAdmin() {
  const agent = useMainAgent();
  const state = usePanelRead<{ actions: ActionView[] }>("/actions/workspace", 0);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [reloads, setReloads] = useState(0);
  const available = state.phase === "ready" && state.payload.actions.some((action) => action.name === "export");
  const exports = usePanelRead<ExportView>(available ? "/workspace/export" : null, reloads, 3000);
  const current = exports.phase === "ready" ? exports.payload.export : null;
  const preparing = current?.status === "queued" || current?.status === "preparing";

  async function exportWorkspace(action: ActionView) {
    if (!agent || busy) return;
    setConfirming(false);
    setBusy(true);
    setNotice(QUIET);
    const outcome = await postAction(agent.id, action.call, {});
    setBusy(false);
    setNotice(outcomeNotice(outcome));
    if (outcome.applied) setReloads((value) => value + 1);
  }

  return (
    <Panel state={state} shape="form">
      {(held) => {
        const action = held.actions.find((entry) => entry.name === "export");
        return (
          <>
            <OutcomeNotice state={notice} />
            <Group title="Workspace data" action={action ? (
              <Button disabled={!agent || busy || preparing} busy={busy} onClick={() => setConfirming(true)}>
                {busy ? "Requesting export" : action.label}
              </Button>
            ) : undefined}>
              <Facts rows={[
                { label: "Export", value: "Conversations, original files, and memory", block: true },
                { label: "Access", value: "All members’ private and shared data, including private-agent and room conversations.", block: true },
                { label: "Format", value: "TAR archive with numbered JSON record files and original files. The manifest lists exclusions.", block: true },
              ]} />
            </Group>
            {action ? (
              <Dialog open={confirming} onOpenChange={setConfirming}>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>Export all member data?</DialogTitle>
                    <DialogDescription>{action.confirm}</DialogDescription>
                  </DialogHeader>
                  <DialogFooter>
                    <Button variant="send" disabled={!agent || busy} onClick={() => void exportWorkspace(action)}>
                      Export all member data
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            ) : null}
            {!action ? <p>Workspace export is not available.</p> : null}
            {action ? <Panel state={exports} shape="form">
              {(value) => value.export ? (
                <Group title="Latest export" action={value.export.status === "ready" ? (
                  <a href={`/surface/web/workspace/export/${encodeURIComponent(value.export.id)}/download`} className={buttonVariants()}>Download export</a>
                ) : undefined}>
                  <Facts rows={[
                    { label: "Status", value: ({ queued: "Queued", preparing: "Preparing", ready: "Ready", failed: "Failed", expired: "Expired" })[value.export.status] },
                    { label: "Download", value: "Available for 24 hours after the export is ready.", block: true },
                    ...(value.export.error ? [{ label: "Error", value: value.export.error, block: true }] : []),
                  ]} />
                </Group>
              ) : null}
            </Panel> : null}
          </>
        );
      }}
    </Panel>
  );
}
