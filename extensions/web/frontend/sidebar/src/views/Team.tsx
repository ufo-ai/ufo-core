import { useRef, useState, type FormEvent } from "react";

import type { Placement } from "@/kernel/pager";
import { Button, ConfirmButton } from "@/components/ui/button";
import { Hint, Input } from "@/components/ui/field";
import { Filter } from "@/components/ui/filter";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ACTS, Td, TdActs, TdFact } from "@/components/ui/table";
import { Sheet } from "@/components/ui/sheet";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { PageToolbar, usePageAct, usePageSearch } from "@/kernel/pane";
import {
  type NoticeState,
  OutcomeNotice,
  Panel,
  QUIET,
  Section,
  outcomeNotice,
  usePanelRead,
} from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
import { postIntent, postKindAction } from "@/lib/api";
import { useMainAgent } from "@/lib/mainAgent";
import type { Member } from "@/lib/types";

type Roster = { members: Member[]; can_manage: boolean };

type Draft = { email: string; admin: boolean };

/** The whole of one member row as the `member` kind takes it: an apply carries the complete spec,
 *  so a control changing one field states the other unchanged. */
type MemberSpec = { admin: boolean; seated: boolean };

const ADMINS = "admins";
const ROLES = [
  { label: "Admins", value: ADMINS },
  { label: "Members", value: "members" },
];

const BLANK: Draft = { email: "", admin: false };

const ADD_MEMBERS_NOTE = "add-members-note";

/** What a member row states: the address it is held under, whether they administer the workspace,
 *  and whether the workspace answers them at all. An admin's acts stand after these, so the roster
 *  they read takes the same columns with one added. */
const MEMBER_COLUMNS = [
  "Member",
  { label: "Role", fact: true },
  { label: "Status", fact: true },
];

const MANAGED_COLUMNS = [...MEMBER_COLUMNS, ""];

/** The three cells of one member row. Access is either live or it is not, and it is said the same
 *  word wherever it is stated. */
function MemberCells({ member }: { member: Member }) {
  return (
    <>
      <Td>{member.email}</Td>
      <TdFact>{member.admin ? "Admin" : "Member"}</TdFact>
      <TdFact>{member.seated ? "Active" : "Disabled"}</TdFact>
    </>
  );
}

/** The acts an admin holds over one member: their role, and whether the workspace answers them.
 *  Both are one `apply` on the member kind, riding the main agent's lane, where the kind's own
 *  guards answer — the last admin keeps the role and the last active admin keeps the access. */
function MemberActs({
  member,
  onApply,
}: {
  member: Member;
  onApply: (spec: MemberSpec) => void;
}) {
  return (
    <TdActs>
      <div className={ACTS}>
        {member.admin ? (
          <ConfirmButton
            verb="Remove admin"
            variant="row"
            onClick={() => onApply({ admin: false, seated: Boolean(member.seated) })}
          />
        ) : (
          <Button
            variant="row"
            onClick={() => onApply({ admin: true, seated: Boolean(member.seated) })}
          >
            Make admin
          </Button>
        )}
        {member.seated ? (
          <ConfirmButton
            verb="Disable"
            variant="row"
            onClick={() => onApply({ admin: member.admin, seated: false })}
          />
        ) : (
          <Button variant="row" onClick={() => onApply({ admin: member.admin, seated: true })}>
            Enable
          </Button>
        )}
      </div>
    </TdActs>
  );
}

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
  const query = place.q ?? "";
  const [role, setRole] = useState("");
  const [adding, setAdding] = useState(false);
  const [drafts, setDrafts] = useState<Draft[]>([BLANK]);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [applied, setApplied] = useState<NoticeState>(QUIET);
  const [busy, setBusy] = useState(false);
  const landed = useRef<string[]>([]);
  const [reloads, setReloads] = useState(0);
  const state = usePanelRead<Roster>("/workspace/team", reloads);
  const ready = drafts.every((row) => row.email.trim());

  async function apply(member: Member, spec: MemberSpec) {
    if (!mainAgent || !member.id) return;
    const outcome = await postIntent(mainAgent.id, {
      verb: "apply",
      kind: "member",
      name: member.id,
      spec,
    });
    if (outcome.applied) setReloads((count) => count + 1);
    setApplied(outcomeNotice(outcome));
  }

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
        await postKindAction(mainAgent.id, "member", "add_member", {
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

  const sheet = adding ? (
    <Sheet open title="Add members" describedBy={ADD_MEMBERS_NOTE} onClose={close}>
        <OutcomeNotice state={notice} />
        <form onSubmit={add} className="flex flex-col items-start gap-sm">
          <Hint id={ADD_MEMBERS_NOTE} className="m-0">
            Each address is added to this workspace, at any email domain.
          </Hint>
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
          <div className="flex w-full justify-end">
            <Button type="submit" variant="send" size="bar" busy={busy} disabled={!ready}>
              Add members
            </Button>
          </div>
        </form>
    </Sheet>
  ) : null;

  const act = usePageAct(
    state.phase === "ready" && state.payload.can_manage && mainAgent ? (
      <Button variant="send" size="bar" onClick={open}>
        Add member
      </Button>
    ) : null,
  );

  const search = usePageSearch();

  return (
    <>
      {act}
      {search ? <PageToolbar /> : null}
      <Panel state={state}>
        {({ members, can_manage }) => {
          const found = members.filter(
            (entry) =>
              entry.email.toLowerCase().includes(query.trim().toLowerCase()) &&
              (!role || (role === ADMINS) === Boolean(entry.admin)),
          );
          return (
            <Section bar={<Filter options={ROLES} value={role} onChange={setRole} />}>
              <OutcomeNotice state={applied} />
              <DataTable
                columns={can_manage ? MANAGED_COLUMNS : MEMBER_COLUMNS}
                rows={found}
                rowKey={(entry) => entry.email}
                empty="This workspace has no members yet."
                note={query || role ? "No member matches this search." : undefined}
              >
                {(entry) => (
                  <>
                    <MemberCells member={entry} />
                    {can_manage ? (
                      <MemberActs member={entry} onApply={(spec) => void apply(entry, spec)} />
                    ) : null}
                  </>
                )}
              </DataTable>
            </Section>
          );
        }}
      </Panel>
      {sheet}
      <Toast state={toast} onDone={() => setToast(SILENT)} />
    </>
  );
}
