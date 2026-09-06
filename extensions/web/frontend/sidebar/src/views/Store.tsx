import { useState } from "react";
import { IconCirclePlus } from "@tabler/icons-react";

import { Button, ConfirmButton } from "@/components/ui/button";
import { ACTS, Lede, Td, TdActs } from "@/components/ui/table";
import { COLUMN, Header, Pane } from "@/kernel/pane";
import { OutcomeNotice, QUIET, Section, outcomeNotice, type NoticeState } from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { postIntent, postObjectAction } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";
import { openAgent } from "@/lib/router";
import type { Agent, ArchivedApp, Member } from "@/lib/types";
import { APP_CREATOR_TITLE } from "@/views/AppBuilder";
import { useApps } from "@/views/Apps";

export const APP_STORE_TITLE = "App Store";
const APP_CREATOR_PURPOSE = "Build an app of your own with the main agent.";
const INSTALL = "Install";
const REMOVE = "Remove";
const RESTORE_ACTION = "restore_application";

/** One row of the store: a shipped app the workspace holds, one it removed, or the act that builds
 *  a new one. */
type Listing =
  | { key: string; kind: "installed"; agent: Agent }
  | { key: string; kind: "removed"; app: ArchivedApp }
  | { key: string; kind: "creator" };

function listingName(row: Listing): string {
  switch (row.kind) {
    case "installed":
      return agentName(row.agent.name);
    case "removed":
      return agentName(row.app.name);
    case "creator":
      return APP_CREATOR_TITLE;
  }
}

/** The deploy's apps in name order — the ones the workspace holds live and the ones it removed, one
 *  list, since a member scanning for an app does not know which state it is in — and the act that
 *  builds one last, where the apps list carried it before the store did. The chat app is not
 *  listed: it is the main agent's row, which the New chat row above the list is the way to, and a
 *  member can neither install nor remove it. An app the deploy withholds is listed nowhere, live or
 *  archived. */
function listings(agents: Agent[], archived: ArchivedApp[]): Listing[] {
  const shipped: Listing[] = [
    ...agents
      .filter((agent) => agent.app && !agent.main && !agent.hidden)
      .map((agent): Listing => ({ key: agent.id, kind: "installed", agent })),
    ...archived
      .filter((app) => app.app && !app.hidden)
      .map((app): Listing => ({ key: app.id, kind: "removed", app })),
  ];
  shipped.sort((left, right) => listingName(left).localeCompare(listingName(right)));
  return shipped.concat({ key: "creator", kind: "creator" });
}

/** The app store: every app the deploy ships, as installed or as one to install, and App Creator
 *  for the app it does not. Installing a shipped app is restoring the row the workspace archived,
 *  through the same `restore_application` action the Apps tab's archived filter posts; removing one
 *  is the archive the app's own settings offer. Both are admin acts because a shipped app has no
 *  owner — the row answers to admins alone — so a member who is not one reads the installed apps and
 *  the build act and nothing they cannot press. Either act re-reads the boot payload the rows are
 *  drawn from, so the row moves between states with no signal of its own. */
export function Store({
  member,
  onBuild,
}: {
  member: Member;
  /** Raises the app-building wizard, which is the store's own last row. */
  onBuild: () => void;
}) {
  const { agents, archived, onRestored } = useApps();
  const mainAgent = useMainAgent();
  const [acting, setActing] = useState<string | null>(null);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const admin = member.admin === true;

  async function act(key: string, post: () => Promise<{ applied: boolean; message: string }>) {
    if (acting !== null) return;
    setActing(key);
    setNotice(QUIET);
    const outcome = await post();
    setActing(null);
    if (!outcome.applied) {
      setNotice(outcomeNotice(outcome));
      return;
    }
    onRestored();
  }

  function install(app: ArchivedApp) {
    if (!mainAgent) return;
    void act(app.id, () =>
      postObjectAction(
        mainAgent.id,
        { kind: "agent", name: app.object, action: RESTORE_ACTION },
        { new_name: app.name },
      ),
    );
  }

  function remove(agent: Agent) {
    void act(agent.id, () =>
      postIntent(agent.id, { verb: "delete", kind: "agent", name: agent.name }),
    );
  }

  return (
    <Pane>
      <section aria-label={APP_STORE_TITLE} className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Header heading={1} title={APP_STORE_TITLE} pinned />
        <div className={cn(COLUMN, "flex-1 overflow-y-auto p-2xl")}>
          <Section>
            <DataTable
              columns={["Application", "Purpose", { label: "", fact: true }]}
              rows={listings(agents, archived)}
              rowKey={(row) => row.key}
              empty="No apps."
              open={(row) => {
                switch (row.kind) {
                  case "installed":
                    return () => openAgent(row.agent.id);
                  case "creator":
                    return onBuild;
                  case "removed":
                    return null;
                }
              }}
            >
              {(row) => (
                <>
                  <Td>
                    <Lede
                      mark={
                        row.kind === "creator" ? (
                          <IconCirclePlus className="size-(--size-glyph)" aria-hidden />
                        ) : (
                          <AgentIcon name={row.kind === "installed" ? row.agent.icon : row.app.icon} />
                        )
                      }
                    >
                      {listingName(row)}
                    </Lede>
                  </Td>
                  <Td className="text-ink-soft">
                    {row.kind === "creator"
                      ? APP_CREATOR_PURPOSE
                      : (row.kind === "installed" ? row.agent.purpose : row.app.purpose) ?? ""}
                  </Td>
                  <TdActs>
                    <div className={ACTS}>
                      {admin && row.kind === "installed" ? (
                        <ConfirmButton
                          verb={REMOVE}
                          variant="row"
                          busy={acting === row.key}
                          onClick={() => remove(row.agent)}
                        />
                      ) : null}
                      {admin && row.kind === "removed" ? (
                        <Button
                          variant="row"
                          busy={acting === row.key}
                          onClick={() => install(row.app)}
                        >
                          {INSTALL}
                        </Button>
                      ) : null}
                    </div>
                  </TdActs>
                </>
              )}
            </DataTable>
            <OutcomeNotice state={notice} />
          </Section>
        </div>
      </section>
    </Pane>
  );
}
