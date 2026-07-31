import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { Td } from "@/components/ui/table";
import { FormFromSchema, initialSpecValue, type SpecValue } from "@/kernel/form";
import { Notice, Panel, Section, usePanelRead } from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
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

  return (
    <Panel state={state}>
      {(payload) => (
        <>
          {notice ? <Notice>{notice}</Notice> : null}
          <DataTable
            columns={[
              "name",
              "schedule",
              "task",
              "creator",
              "state",
              "next run",
              "last run",
              "expires",
              "",
            ]}
            rows={payload.tasks}
            rowKey={(task) => task.name}
            empty="No scheduled tasks for this agent."
          >
            {(task) => (
              <>
                <Td>{task.name}</Td>
                <Td>{task.schedule}</Td>
                <Td>{task.description || task.prompt || "private member task"}</Td>
                <Td>{task.created_by || "—"}</Td>
                <Td>{task.paused ? "paused" : "scheduled"}</Td>
                <Td>{day(task.next_run_at) || "—"}</Td>
                <Td>{day(task.last_run_at) || "—"}</Td>
                <Td>{day(task.expires_at) || "—"}</Td>
                <Td>
                  {payload.spec_schema ? (
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
              </>
            )}
          </DataTable>
          {payload.spec_schema ? (
            editing ? (
              <Section title={"Edit " + editing.name}>
                <TaskForm schema={payload.spec_schema} task={editing} onDone={act} />
              </Section>
            ) : (
              <Section title="New task">
                <TaskForm schema={payload.spec_schema} task={null} onDone={act} />
              </Section>
            )
          ) : null}
        </>
      )}
    </Panel>
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
      editable.map((key) => [key, initialSpecValue(properties[key] ?? {}, current[key])]),
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
      <FormFromSchema
        schema={schema}
        fields={editable}
        values={values}
        onChange={(key, value) => setValues((state) => ({ ...state, [key]: value }))}
      />
      <Button type="submit" variant="send" disabled={busy}>
        {task ? "Save task" : "Create task"}
      </Button>
    </form>
  );
}
