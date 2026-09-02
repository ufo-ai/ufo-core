import { useState } from "react";

import type { Placement } from "@/kernel/pager";
import { Button, ConfirmButton } from "@/components/ui/button";
import { Filter } from "@/components/ui/filter";
import { ACTS, Td, TdActs, TdFact } from "@/components/ui/table";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { ActionControls } from "@/kernel/action";
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
import { postAction, postIntent } from "@/lib/api";
import { useMainAgent } from "@/lib/mainAgent";
import type { ActionView, Member } from "@/lib/types";

type Roster = { members: Member[]; can_manage: boolean; actions: ActionView[] };

/** The whole of one member row as the `member` kind takes it: an apply carries the complete spec,
 *  so a control changing one field states the other unchanged. */
type MemberSpec = { admin: boolean; seated: boolean };

const ADMINS = "admins";
const ROLES = [
  { label: "Admins", value: ADMINS },
  { label: "Members", value: "members" },
];

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

/** The roster, and the acts an admin holds over it — the add drawn from the member collection's
 *  own action schema, the role and access changes drawn as an apply on each row, both on the main
 *  agent's lane. `can_manage` keeps them off a screen whose reader the verbs would refuse; their
 *  gates still decide. */
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
  const [applied, setApplied] = useState<NoticeState>(QUIET);
  const [reloads, setReloads] = useState(0);
  const state = usePanelRead<Roster>("/workspace/team", reloads);

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

  const act = usePageAct(
    state.phase === "ready" && state.payload.can_manage && mainAgent ? (
      <ActionControls
        views={state.payload.actions}
        post={(view, input) => postAction(mainAgent.id, view.call, input)}
        onApplied={(_view, outcome) => onPlace({ notice: outcome.message })}
      />
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
      <Toast state={toast} onDone={() => setToast(SILENT)} />
    </>
  );
}
