import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/field";
import { Table, Td, Th } from "@/components/ui/table";
import { Notice, PanelEmpty, usePanelRead } from "@/kernel/panel";
import { postIntent } from "@/lib/api";
import type { Agent } from "@/lib/types";

type Skill = { name: string; description: string; origin: string };

export function Skills({ agent }: { agent: Agent }) {
  const [reloads, setReloads] = useState(0);
  const [notice, setNotice] = useState("");
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const state = usePanelRead<{ skills: Skill[] }>("/agents/" + agent.id + "/skills", reloads);

  async function submitIntent(intent: unknown) {
    setBusy(true);
    const outcome = await postIntent(agent.id, intent);
    setBusy(false);
    setNotice(outcome.message);
    if (outcome.applied) setReloads((count) => count + 1);
  }

  if (state.phase === "loading") return null;
  if (state.phase === "failed") return <PanelEmpty>{state.message}</PanelEmpty>;

  const listed = state.payload.skills;
  const custom = listed.filter((skill) => skill.origin === "member");
  const deploy = listed.filter((skill) => skill.origin === "deploy");

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
    <>
      <h2 className="mb-2xs mt-xl text-label opacity-(--muted-soft)">This agent's skills</h2>
      {!custom.length ? (
        <PanelEmpty>No member-authored skills for {agent.name}.</PanelEmpty>
      ) : (
        <Table>
          <thead>
            <tr>
              {["name", "description", ""].map((column, index) => (
                <Th key={index}>{column}</Th>
              ))}
            </tr>
          </thead>
          <tbody>
            {custom.map((skill) => (
              <tr key={skill.name}>
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
              </tr>
            ))}
          </tbody>
        </Table>
      )}

      <h2 className="mb-2xs mt-xl text-label opacity-(--muted-soft)">Deploy skills</h2>
      {!deploy.length ? (
        <PanelEmpty>No deploy skills.</PanelEmpty>
      ) : (
        <Table>
          <thead>
            <tr>
              {["name", "description"].map((column) => (
                <Th key={column}>{column}</Th>
              ))}
            </tr>
          </thead>
          <tbody>
            {deploy.map((skill) => (
              <tr key={skill.name}>
                <Td>{skill.name}</Td>
                <Td>{skill.description}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}

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
      <Notice>{notice}</Notice>
    </>
  );
}
