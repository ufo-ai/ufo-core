import { IconDotsVertical, IconPlayerPause } from "@tabler/icons-react";
import { useState } from "react";

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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Filter } from "@/components/ui/filter";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ACTS, TdActs, TdFact, TdFill, TdWhole } from "@/components/ui/table";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { ActionControls } from "@/kernel/action";
import { PageToolbar } from "@/kernel/pane";
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
import { cn } from "@/lib/cn";
import { MemberAvatar, faceName } from "@/lib/memberFace";
import { useMainAgent } from "@/lib/mainAgent";
import type { ActionView, Member } from "@/lib/types";

type Roster = { members: Member[]; can_manage: boolean; actions: ActionView[] };

type MemberSpec = { admin: boolean; seated: boolean };

type Pending = { member: Member; spec: MemberSpec; title: string; body: string; verb: string };

const ADMINS = "admins";
const ROLES = [
  { label: "Admins", value: ADMINS },
  { label: "Members", value: "members" },
];

const MEMBER_COLUMNS = [
  { label: "Member", fill: true },
  { label: "Email", whole: true },
  { label: "Role", fact: true },
];

const MANAGED_COLUMNS = [...MEMBER_COLUMNS, { label: "", acts: true }];

/** Who the row is about and how to reach them are what the member came to read; the role recedes. */
const FACT = "text-ink";
/** A disabled member recedes but keeps every word they hold. The dimming rides the cells rather
 *  than the row, so the fill under the pointer stays at full strength. */
const RESTING = "opacity-(--opacity-muted-strong)";
const MARK = "size-3.5 shrink-0 text-ink-soft";
const DISABLED = "Disabled";

/** A disabled member reaches nothing, so they sit under the ones who do. A sort holds equal keys in
 *  the order it was given them, so each band keeps the order the read answered in. */
function seatedFirst(members: Member[]): Member[] {
  return [...members].sort((one, two) => Number(Boolean(two.seated)) - Number(Boolean(one.seated)));
}

/** Every cell holds one line, because a row is one record high (`--size-record`) — a stack of two
 *  inside it has no room around them and stands the roster taller than every other table. */
function MemberCells({ member }: { member: Member }) {
  const rested = member.seated ? undefined : RESTING;
  return (
    <>
      <TdFill className={cn(FACT, rested)}>
        <span className="flex min-w-0 items-center gap-sm">
          <MemberAvatar face={member} plain />
          <span className="truncate">{faceName(member)}</span>
          {member.seated ? null : (
            <Tooltip>
              <TooltipTrigger asChild>
                <IconPlayerPause className={MARK} role="img" aria-label={DISABLED} />
              </TooltipTrigger>
              <TooltipContent side="top">{DISABLED}</TooltipContent>
            </Tooltip>
          )}
        </span>
      </TdFill>
      <TdWhole className={cn(FACT, rested)}>{member.email}</TdWhole>
      <TdFact className={rested}>{member.admin ? "Admin" : "Member"}</TdFact>
    </>
  );
}

/** Every act on a member is reversible, so the two that take something away ask first and the two
 *  that give it back do not. */
function MemberActs({
  member,
  busy,
  onApply,
  onAsk,
}: {
  member: Member;
  busy: boolean;
  onApply: (spec: MemberSpec) => void;
  onAsk: (pending: Pending) => void;
}) {
  const named = faceName(member);
  const seated = Boolean(member.seated);
  return (
    <TdActs className={member.seated ? undefined : RESTING}>
      <div className={ACTS}>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="quiet" size="icon" disabled={busy} aria-label={"Actions for " + named}>
              <IconDotsVertical aria-hidden />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {member.admin ? (
              <DropdownMenuItem
                onSelect={() =>
                  onAsk({
                    member,
                    spec: { admin: false, seated },
                    title: "Remove " + named + " as an admin?",
                    body: "They keep their access and stop managing members.",
                    verb: "Remove admin",
                  })
                }
              >
                Remove admin
              </DropdownMenuItem>
            ) : (
              <DropdownMenuItem onSelect={() => onApply({ admin: true, seated })}>
                Make admin
              </DropdownMenuItem>
            )}
            {seated ? (
              <DropdownMenuItem
                onSelect={() =>
                  onAsk({
                    member,
                    spec: { admin: Boolean(member.admin), seated: false },
                    title: "Disable " + named + "?",
                    body: "They lose access to this workspace until somebody enables them again.",
                    verb: "Disable",
                  })
                }
              >
                Disable
              </DropdownMenuItem>
            ) : (
              <DropdownMenuItem
                onSelect={() => onApply({ admin: Boolean(member.admin), seated: true })}
              >
                Enable
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </TdActs>
  );
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
  const [applied, setApplied] = useState<NoticeState>(QUIET);
  const [reloads, setReloads] = useState(0);
  const [asking, setAsking] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const state = usePanelRead<Roster>("/workspace/team", reloads);

  async function apply(member: Member, spec: MemberSpec) {
    if (busy) return;
    if (!mainAgent || !member.id) {
      setAsking(null);
      setApplied(outcomeNotice({ applied: false, message: "This member cannot be changed here." }));
      return;
    }
    setBusy(true);
    const outcome = await postIntent(mainAgent.id, {
      verb: "apply",
      kind: "member",
      name: member.id,
      spec,
    });
    setBusy(false);
    if (outcome.applied) setReloads((count) => count + 1);
    setAsking(null);
    setApplied(outcomeNotice(outcome));
  }

  const manages = state.phase === "ready" && state.payload.can_manage && mainAgent !== null;

  return (
    <>
      <OutcomeNotice state={applied} />
      <PageToolbar>
        {manages && state.phase === "ready" && mainAgent ? (
          <div className="ml-auto flex shrink-0 items-center">
            <ActionControls
              views={state.payload.actions}
              post={(view, input) => postAction(mainAgent.id, view.call, input)}
              onApplied={(_view, outcome) => onPlace({ notice: outcome.message })}
            />
          </div>
        ) : null}
      </PageToolbar>
      <Section bar={<Filter options={ROLES} value={role} onChange={setRole} />}>
        <Panel state={state}>
          {({ members, can_manage }) => {
            const wanted = query.trim().toLowerCase();
            const found = members.filter(
              (entry) =>
                (entry.email.toLowerCase().includes(wanted) ||
                  faceName(entry).toLowerCase().includes(wanted)) &&
                (!role || (role === ADMINS) === Boolean(entry.admin)),
            );
            return (
              <DataTable
                columns={can_manage ? MANAGED_COLUMNS : MEMBER_COLUMNS}
                rows={seatedFirst(found)}
                rowKey={(entry) => entry.email}
                lede
                empty="This workspace has no members yet."
                note={query || role ? "No member matches this search." : undefined}
              >
                {(entry) => (
                  <>
                    <MemberCells member={entry} />
                    {can_manage ? (
                      <MemberActs
                        member={entry}
                        busy={busy}
                        onApply={(spec) => void apply(entry, spec)}
                        onAsk={setAsking}
                      />
                    ) : null}
                  </>
                )}
              </DataTable>
            );
          }}
        </Panel>
      </Section>
      <Dialog open={asking !== null} onOpenChange={(next) => (next ? undefined : setAsking(null))}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{asking?.title}</DialogTitle>
            <DialogDescription>{asking?.body}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              busy={busy}
              onClick={() => asking && void apply(asking.member, asking.spec)}
            >
              {asking?.verb}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Toast state={toast} onDone={() => setToast(SILENT)} />
    </>
  );
}
