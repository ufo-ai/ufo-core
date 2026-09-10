import { IconSettings } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import type { ObjectRow, ObjectValue } from "@/kernel/objects";
import { COLUMN, Header, Page, Pane } from "@/kernel/pane";
import { Panel, PanelBlank, usePanelRead } from "@/kernel/panel";
import { RowLines } from "@/kernel/rows";
import { useConversationTitle } from "@/lib/conversationLink";
import { Moment } from "@/lib/moments";
import { openChat, placeWorkspace } from "@/lib/router";
import { WORKSPACE_VIEWS } from "@/views/registry";

const SETTINGS = "Task settings";
const NO_THREADS = "No task thread is visible to you.";
const THREAD_WORD = "Thread";
const NEVER_RAN = "No run yet";
const PAUSED = "Paused";
const BOUND = "The threads that ran most recently. " + SETTINGS + " holds the rest.";

/** Ordered on the moment `threads` sorts the rows it draws by, because the page the backend
 *  answers with is cut on this field: any other order drops threads the screen ranks first. */
const TASKS_READ = "/objects/scheduled_task?order_by=last_run_at&order=desc";

type TasksPayload = { objects: ObjectRow[]; next_cursor: string | null };

/** One conversation every task reporting into it, because that thread is what a member opens: two
 *  tasks posting into the same chat are one row, not two rows onto the same transcript. */
type Thread = { conversationId: string; tasks: ObjectRow[]; ran: string | null };

function said(value: ObjectValue | undefined): string {
  return typeof value === "string" ? value : "";
}

function threads(rows: ObjectRow[]): Thread[] {
  const held = new Map<string, Thread>();
  for (const row of rows) {
    const conversationId = said(row.conversation);
    if (!conversationId) continue;
    const thread = held.get(conversationId) ?? { conversationId, tasks: [], ran: null };
    const ran = said(row.last_run_at) || null;
    held.set(conversationId, {
      conversationId,
      tasks: [...thread.tasks, row],
      ran: ran !== null && (thread.ran === null || ran > thread.ran) ? ran : thread.ran,
    });
  }
  return [...held.values()].sort((one, other) => (other.ran ?? "").localeCompare(one.ran ?? ""));
}

function ThreadName({ thread }: { thread: Thread }) {
  const title = useConversationTitle(thread.conversationId);
  return <>{title || said(thread.tasks[0].origin) || THREAD_WORD}</>;
}

/** The threads the workspace's scheduled tasks report into, and behind the gear the tasks
 *  themselves: what a task is set to do is a setting, and reading what it did is not.
 *
 *  A row is a thread and the page envelope counts tasks, so one page of tasks does not page the
 *  threads it groups into: the screen draws the threads of the first page and says the rest are
 *  behind the gear, where the tasks themselves page one by one. */
export function TaskThreads() {
  const state = usePanelRead<TasksPayload>(TASKS_READ);
  return (
    <Pane>
      <Header
        pinned
        heading={1}
        title={WORKSPACE_VIEWS.tasks.label}
        acts={
          <Button
            variant="quiet"
            size="icon"
            aria-label={SETTINGS}
            onClick={() => placeWorkspace("tasks", {}, "push")}
          >
            <IconSettings aria-hidden />
          </Button>
        }
      />
      <Page>
        <div className={COLUMN}>
          <Panel state={state}>
            {(payload) => {
              const rows = threads(payload.objects);
              if (!rows.length) return <PanelBlank body={NO_THREADS} />;
              return (
                <div className="flex flex-col">
                  <RowLines
                    rows={rows}
                    rowKey={(thread) => thread.conversationId}
                    primary={(thread) => <ThreadName thread={thread} />}
                    meta={(thread) => [
                      thread.tasks.map((task) => task.summary).join(" · "),
                      thread.tasks.every((task) => task.paused === true) ? PAUSED : "",
                    ]}
                    when={(thread) =>
                      thread.ran === null ? NEVER_RAN : <Moment at={thread.ran} />
                    }
                    open={(thread) => () => openChat(thread.conversationId)}
                  />
                  {payload.next_cursor ? (
                    <p className="m-0 px-lg py-lg text-label text-ink-soft">{BOUND}</p>
                  ) : null}
                </div>
              );
            }}
          </Panel>
        </div>
      </Page>
    </Pane>
  );
}
