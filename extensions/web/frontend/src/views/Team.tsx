import { useRef, useState, type FormEvent } from "react";

import type { Placement } from "@/kernel/pager";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/field";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Td } from "@/components/ui/table";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import {
  type NoticeState,
  OutcomeNotice,
  Panel,
  QUIET,
  Section,
  usePanelRead,
} from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
import { postIntent } from "@/lib/api";
import { useMainAgent } from "@/lib/mainAgent";

type Member = { email: string; admin: boolean; seated: boolean };

type Roster = { members: Member[]; can_add: boolean };

type Draft = { email: string; admin: boolean };

const BLANK: Draft = { email: "", admin: false };

function counted(landed: string[]) {
  return landed.length === 1 ? landed[0] : landed.length + " members added.";
}

export function Team({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const mainAgent = useMainAgent();
  const [toast, setToast] = useState<ToastState>(place.notice ? { title: place.notice } : SILENT);
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const [drafts, setDrafts] = useState<Draft[]>([BLANK]);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [busy, setBusy] = useState(false);
  const landed = useRef<string[]>([]);
  const [reloads, setReloads] = useState(0);
  const state = usePanelRead<Roster>("/workspace/team", reloads);
  const ready = drafts.every((row) => row.email.trim());

  function open() {
    setDrafts([BLANK]);
    setNotice(QUIET);
    landed.current = [];
    setAdding(true);
  }

  function close() {
    setAdding(false);
    if (!landed.current.length) return;
    onPlace({ notice: counted(landed.current) });
  }

  function edit(index: number, patch: Partial<Draft>) {
    setDrafts((rows) => rows.map((row, at) => (at === index ? { ...row, ...patch } : row)));
  }

  async function add(event: FormEvent) {
    event.preventDefault();
    if (busy || !ready || !mainAgent) return;
    const wanted = drafts.map((row) => ({ ...row, email: row.email.trim() }));
    setBusy(true);
    const outcomes: { applied: boolean; message: string }[] = [];
    for (const row of wanted) {
      outcomes.push(
        await postIntent(mainAgent.id, {
          verb: "add_member",
          email: row.email,
          admin: row.admin,
        }),
      );
    }
    setBusy(false);
    landed.current.push(
      ...outcomes.filter((outcome) => outcome.applied).map((outcome) => outcome.message),
    );
    const refused = wanted.filter((_, at) => !outcomes[at].applied);
    if (!refused.length) {
      close();
      return;
    }
    setDrafts(refused);
    setNotice({
      text: outcomes
        .filter((outcome) => !outcome.applied)
        .map((outcome) => outcome.message)
        .join(" "),
      refused: true,
    });
  }

  return (
    <Panel state={state}>
      {({ members, can_add }) => {
        const found = members.filter((entry) =>
          entry.email.toLowerCase().includes(query.trim().toLowerCase()),
        );
        return (
          <>
            <Section
              title="Members"
              bar={
                <>
                  <Input
                    type="search"
                    aria-label="Search members"
                    placeholder="Search"
                    className="max-w-control-row"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                  />
                  {can_add && mainAgent ? (
                    <Button variant="send" onClick={open}>
                      Add member
                    </Button>
                  ) : null}
                  <Button onClick={() => setReloads((count) => count + 1)}>Refresh</Button>
                </>
              }
            >
              <DataTable
                columns={["Member", "Role", "Seat"]}
                rows={found}
                rowKey={(entry) => entry.email}
                empty="This workspace has no members yet."
                note={query ? "No member matches that search." : undefined}
              >
                {(entry) => (
                  <>
                    <Td>{entry.email}</Td>
                    <Td>{entry.admin ? "Admin" : "Member"}</Td>
                    <Td>{entry.seated ? "Seated" : "No seat"}</Td>
                  </>
                )}
              </DataTable>
            </Section>
            <Dialog open={adding} onOpenChange={(next) => (next ? open() : close())}>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Add Members</DialogTitle>
                  <DialogDescription>
                    Each address is added to this workspace, at any email domain.
                  </DialogDescription>
                </DialogHeader>
                <OutcomeNotice state={notice} />
                <form id="add-member" onSubmit={add} className="flex flex-col items-start gap-sm">
                  {drafts.map((row, index) => (
                    <div key={index} className="flex w-full items-stretch gap-sm">
                      <Input
                        type="email"
                        required
                        aria-label={drafts.length > 1 ? "Email " + (index + 1) : "Email"}
                        placeholder="email@work.com"
                        className="max-w-none flex-1"
                        value={row.email}
                        onChange={(event) => edit(index, { email: event.target.value })}
                      />
                      <Select
                        value={row.admin ? "admin" : "member"}
                        onValueChange={(value) => edit(index, { admin: value === "admin" })}
                      >
                        <SelectTrigger
                          aria-label={drafts.length > 1 ? "Role " + (index + 1) : "Role"}
                          className="max-w-control-row"
                        >
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="member">Member</SelectItem>
                          <SelectItem value="admin">Admin</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  ))}
                  <Button type="button" onClick={() => setDrafts((rows) => [...rows, BLANK])}>
                    Add more
                  </Button>
                </form>
                <DialogFooter>
                  <Button
                    type="submit"
                    form="add-member"
                    variant="send"
                    busy={busy}
                    disabled={!ready}
                  >
                    Add members
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
            <Toast state={toast} onDone={() => setToast(SILENT)} />
          </>
        );
      }}
    </Panel>
  );
}
