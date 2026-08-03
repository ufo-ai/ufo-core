import { useState, type FormEvent } from "react";

import type { Placement } from "@/kernel/pager";
import { Button } from "@/components/ui/button";
import { Checkbox, Input } from "@/components/ui/field";
import { Table, Td, Th } from "@/components/ui/table";
import { type NoticeState, OutcomeNotice, Panel, outcomeNotice, usePanelRead } from "@/kernel/panel";
import { postIntent } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";

type Member = { email: string; admin: boolean; seated: boolean };

type Roster = { members: Member[]; can_add: boolean; domain: string | null };

function Strip({ figures }: { figures: { value: number; label: string }[] }) {
  return (
    <div className="mb-lg flex w-fit flex-wrap items-baseline">
      {figures.map((figure, index) => (
        <div
          key={figure.label}
          className={cn(
            "flex items-baseline gap-xs px-2xl",
            index === 0 && "pl-0",
            index > 0 && "border-l border-edge-soft",
          )}
        >
          <span className="font-strong tabular-nums">{figure.value}</span>
          <span className="text-small opacity-(--muted)">{figure.label}</span>
        </div>
      ))}
    </div>
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
  const [notice, setNotice] = useState<NoticeState>({ text: place.notice ?? "", refused: false });
  const [email, setEmail] = useState("");
  const [admin, setAdmin] = useState(false);
  const [busy, setBusy] = useState(false);
  const state = usePanelRead<Roster>("/workspace/team");

  async function add(event: FormEvent) {
    event.preventDefault();
    const address = email.trim();
    if (!address || !mainAgent) return;
    setBusy(true);
    const outcome = await postIntent(mainAgent.id, {
      verb: "add_member",
      email: address,
      admin,
    });
    setBusy(false);
    if (outcome.applied) {
      onPlace({ notice: outcome.message });
      return;
    }
    setNotice(outcomeNotice(outcome));
  }

  return (
    <Panel state={state}>
      {({ members, can_add, domain }) => (
        <>
          <Strip
            figures={[
              { value: members.length, label: "members" },
              { value: members.filter((entry) => entry.admin).length, label: "admins" },
              { value: members.filter((entry) => entry.seated).length, label: "seated" },
            ]}
          />
          <Table>
            <thead>
              <tr>
                {["member", "role", "seat"].map((column) => (
                  <Th key={column}>{column}</Th>
                ))}
              </tr>
            </thead>
            <tbody>
              {members.map((entry) => (
                <tr key={entry.email}>
                  <Td>{entry.email}</Td>
                  <Td>{entry.admin ? "Admin" : "Member"}</Td>
                  <Td>{entry.seated ? "Seated" : "No seat"}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
          {can_add && mainAgent ? (
            <form onSubmit={add} className="mb-lg flex items-center gap-sm">
              <Input
                type="email"
                required
                name="email"
                placeholder={domain ? "email@" + domain : "email@work.com"}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
              <label className="flex items-center gap-hair">
                <Checkbox
                  name="admin"
                  checked={admin}
                  onChange={(event) => setAdmin(event.target.checked)}
                />
                Admin
              </label>
              <Button type="submit" variant="send" disabled={busy}>
                Add member
              </Button>
            </form>
          ) : null}
          <OutcomeNotice state={notice} />
        </>
      )}
    </Panel>
  );
}
