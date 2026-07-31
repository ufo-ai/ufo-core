import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { Table, Td, Th } from "@/components/ui/table";
import { SpecField, initialSpecValue, specType, type SpecValue } from "@/kernel/form";
import { Notice, PanelEmpty, Section, usePanelRead } from "@/kernel/panel";
import { postIntent } from "@/lib/api";
import type { Agent, SchemaProperty } from "@/lib/types";

type Task = {
  name: string;
  schedule: string;
  prompt: string | null;
  description: string | null;
  created_by: string | null;
  paused: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  expires_at: string | null;
};

type TasksPayload = {
  tasks: Task[];
  spec_schema: { properties?: Record<string, SchemaProperty> } | null;
};

export function day(iso: string | null): string | null {
  return iso == null ? null : iso.slice(0, 16).replace("T", " ");
}

export function Tasks({ agent }: { agent: Agent }) {
  const [reloads, setReloads] = useState(0);
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<Task | null>(null);
  const state = usePanelRead<TasksPayload>("/agents/" + agent.id + "/tasks", reloads);

  async function act(envelope: unknown) {
    const outcome = await postIntent(agent.id, envelope);
    setNotice(outcome.message);
    setEditing(null);
    setReloads((count) => count + 1);
  }

  if (state.phase === "loading") return null;
  if (state.phase === "failed") return <PanelEmpty>{state.message}</PanelEmpty>;

  const payload = state.payload;
  const schema = payload.spec_schema;

  return (
    <>
      {notice ? <Notice>{notice}</Notice> : null}
      {!payload.tasks.length ? (
        <PanelEmpty>No scheduled tasks for this agent.</PanelEmpty>
      ) : (
        <Table>
          <thead>
            <tr>
              {[
                "name",
                "schedule",
                "task",
                "creator",
                "state",
                "next run",
                "last run",
                "expires",
                "",
              ].map((column, index) => (
                <Th key={index}>{column}</Th>
              ))}
            </tr>
          </thead>
          <tbody>
            {payload.tasks.map((task) => (
              <tr key={task.name}>
                <Td>{task.name}</Td>
                <Td>{task.schedule}</Td>
                <Td>{task.description || task.prompt || "private member task"}</Td>
                <Td>{task.created_by || "—"}</Td>
                <Td>{task.paused ? "paused" : "scheduled"}</Td>
                <Td>{day(task.next_run_at) || "—"}</Td>
                <Td>{day(task.last_run_at) || "—"}</Td>
                <Td>{day(task.expires_at) || "—"}</Td>
                <Td>
                  {schema ? (
                    <div className="flex flex-wrap gap-xs">
                      <Button variant="row" onClick={() => setEditing(task)}>
                        Edit
                      </Button>
                      <Button
                        variant="row"
                        onClick={() =>
                          act({
                            verb: "apply",
                            kind: "scheduled_task",
                            name: task.name,
                            spec: { paused: !task.paused },
                          })
                        }
                      >
                        {task.paused ? "Resume" : "Pause"}
                      </Button>
                      <Button
                        variant="row"
                        onClick={() =>
                          act({ verb: "delete", kind: "scheduled_task", name: task.name })
                        }
                      >
                        Delete
                      </Button>
                    </div>
                  ) : null}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {schema ? (
        editing ? (
          <Section title={"Edit " + editing.name}>
            <TaskForm schema={schema} task={editing} onDone={act} />
          </Section>
        ) : (
          <Section title="New task">
            <TaskForm schema={schema} task={null} onDone={act} />
          </Section>
        )
      ) : null}
    </>
  );
}

function TaskForm({
  schema,
  task,
  onDone,
}: {
  schema: { properties?: Record<string, SchemaProperty> };
  task: Task | null;
  onDone: (envelope: unknown) => Promise<void>;
}) {
  const properties = schema.properties ?? {};
  const editable = Object.keys(properties).filter(
    (key) =>
      key !== "paused" &&
      (!task || task.prompt !== null || key === "schedule" || key === "expires_at"),
  );
  const current: Record<string, unknown> = task
    ? {
        schedule: task.schedule,
        prompt: task.prompt,
        description: task.description,
        expires_at: task.expires_at,
      }
    : {};
  const [name, setName] = useState(task ? task.name : "");
  const [values, setValues] = useState<Record<string, SpecValue>>(() =>
    Object.fromEntries(
      editable.map((key) => [key, initialSpecValue({ type: "string" }, current[key])]),
    ),
  );
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    const spec: Record<string, string> = {};
    for (const key of editable) {
      const value = String(values[key] ?? "").trim();
      if (value) spec[key] = value;
      else if (task && key === "description") spec[key] = "";
    }
    await onDone({ verb: "apply", kind: "scheduled_task", name: name.trim(), spec });
    setBusy(false);
  }

  return (
    <form onSubmit={submit}>
      <Input
        placeholder="task-name"
        value={name}
        disabled={task !== null}
        onChange={(event) => setName(event.target.value)}
      />
      {editable.map((key) => (
        <SpecField
          key={key}
          name={key}
          prop={{ type: specType(properties[key]) }}
          value={values[key] ?? ""}
          onChange={(value) => setValues((state) => ({ ...state, [key]: value }))}
        />
      ))}
      <Button type="submit" variant="send" disabled={busy}>
        {task ? "Save task" : "Create task"}
      </Button>
    </form>
  );
}
