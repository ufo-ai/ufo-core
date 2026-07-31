import { useState, type FormEvent } from "react";

import type { Placement } from "@/views/Workspace";
import { Button } from "@/components/ui/button";
import { Checkbox, Input } from "@/components/ui/field";
import { Table, Td, Th } from "@/components/ui/table";
import { Notice, PanelEmpty, usePanelRead } from "@/kernel/panel";
import { postIntent } from "@/lib/api";
import { useMainAgent } from "@/lib/mainAgent";

type Member = { email: string; admin: boolean; seated: boolean };

type Roster = { members: Member[]; can_add: boolean; domain: string | null };

export function Team({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const mainAgent = useMainAgent();
  const [notice, setNotice] = useState(place.notice ?? "");
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
    setNotice(outcome.message);
  }

  if (state.phase === "loading") return null;
  if (state.phase === "failed") return <PanelEmpty>{state.message}</PanelEmpty>;

  const { members, can_add, domain } = state.payload;

  return (
    <>
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
      <Notice>{notice}</Notice>
    </>
  );
}
