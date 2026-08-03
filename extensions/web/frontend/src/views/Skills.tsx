import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/field";
import { Td } from "@/components/ui/table";
import { type NoticeState, OutcomeNotice, Panel, QUIET, outcomeNotice, usePanelRead } from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
import { postIntent } from "@/lib/api";
import type { Agent } from "@/lib/types";

type Skill = { name: string; description: string; origin: string };

export function Skills({ agent }: { agent: Agent }) {
  const [reloads, setReloads] = useState(0);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const state = usePanelRead<{ skills: Skill[] }>("/agents/" + agent.id + "/skills", reloads);

  async function submitIntent(intent: unknown) {
    setBusy(true);
    const outcome = await postIntent(agent.id, intent);
    setBusy(false);
    setNotice(outcomeNotice(outcome));
    if (outcome.applied) setReloads((count) => count + 1);
  }

  function save(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || !content.trim()) return;
    submitIntent({
      verb: "apply",
      kind: "skill",
      name: name.trim(),
      spec: { files: { "SKILL.md": content } },
    });
  }

  return (
    <Panel state={state}>
      {(payload) => (
        <>
          <h2 className="mb-2xs mt-xl text-label opacity-(--muted-soft)">
            This agent's skills
          </h2>
          <DataTable
            columns={["name", "description", ""]}
            rows={payload.skills.filter((skill) => skill.origin === "member")}
            rowKey={(skill) => skill.name}
            empty={"No member-authored skills for " + agent.name + "."}
          >
            {(skill) => (
              <>
                <Td>{skill.name}</Td>
                <Td>{skill.description}</Td>
                <Td>
                  <Button
                    variant="row"
                    disabled={busy}
                    onClick={() => submitIntent({ verb: "delete", kind: "skill", name: skill.name })}
                  >
                    Delete
                  </Button>
                </Td>
              </>
            )}
          </DataTable>

          <h2 className="mb-2xs mt-xl text-label opacity-(--muted-soft)">Deploy skills</h2>
          <DataTable
            columns={["name", "description"]}
            rows={payload.skills.filter((skill) => skill.origin === "deploy")}
            rowKey={(skill) => skill.name}
            empty="No deploy skills."
          >
            {(skill) => (
              <>
                <Td>{skill.name}</Td>
                <Td>{skill.description}</Td>
              </>
            )}
          </DataTable>

          <h2 className="mb-2xs mt-xl text-label opacity-(--muted-soft)">Save a skill</h2>
          <form onSubmit={save}>
            <Input
              placeholder="skill-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
            <Textarea
              placeholder={"---\nname: skill-name\ndescription: …\n---\n"}
              value={content}
              onChange={(event) => setContent(event.target.value)}
            />
            <Button type="submit" variant="send" disabled={busy}>
              Save
            </Button>
          </form>
          <OutcomeNotice state={notice} />
        </>
      )}
    </Panel>
  );
}
