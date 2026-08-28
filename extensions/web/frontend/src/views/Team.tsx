import { useState } from "react";

import type { Placement } from "@/kernel/pager";
import { Filter } from "@/components/ui/filter";
import { Td, TdFact } from "@/components/ui/table";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { ActionControls } from "@/kernel/action";
import { PageToolbar, usePageAct, usePageSearch } from "@/kernel/pane";
import { Panel, Section, usePanelRead } from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
import { postAction } from "@/lib/api";
import { useMainAgent } from "@/lib/mainAgent";
import type { ActionView, Member } from "@/lib/types";

type Roster = { members: Member[]; can_add: boolean; actions: ActionView[] };

const ADMINS = "admins";
const ROLES = [
  { label: "Admins", value: ADMINS },
  { label: "Members", value: "members" },
];

/** What a member row states, wherever the workspace lists its members: the address it is held
 *  under, whether they administer it, and whether they hold a seat. Administration stands its own
 *  acts after these, so it takes the same columns with one added. */
export const MEMBER_COLUMNS = [
  "Member",
  { label: "Role", fact: true },
  { label: "Seat", fact: true },
];

/** The three cells of one member row. A seat is either held or it is not, and it is said the same
 *  word on every screen that states it. */
export function MemberCells({ member }: { member: Member }) {
  return (
    <>
      <Td>{member.email}</Td>
      <TdFact>{member.admin ? "Admin" : "Member"}</TdFact>
      <TdFact>{member.seated ? "Seated" : "No seat"}</TdFact>
    </>
  );
}

/** The roster, and the acts the member collection projects beside it — the add drawn from the
 *  action's own schema, on the main agent's lane. `can_add` keeps the act off a screen whose reader
 *  the action would refuse; the action's gate still decides. */
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
  const state = usePanelRead<Roster>("/workspace/team");

  const act = usePageAct(
    state.phase === "ready" && state.payload.can_add && mainAgent ? (
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
        {({ members }) => {
          const found = members.filter(
            (entry) =>
              entry.email.toLowerCase().includes(query.trim().toLowerCase()) &&
              (!role || (role === ADMINS) === Boolean(entry.admin)),
          );
          return (
            <Section bar={<Filter options={ROLES} value={role} onChange={setRole} />}>
              <DataTable
                columns={MEMBER_COLUMNS}
                rows={found}
                rowKey={(entry) => entry.email}
                empty="This workspace has no members yet."
                note={query || role ? "No member matches this search." : undefined}
              >
                {(entry) => <MemberCells member={entry} />}
              </DataTable>
            </Section>
          );
        }}
      </Panel>
      <Toast state={toast} onDone={() => setToast(SILENT)} />
    </>
  );
}
