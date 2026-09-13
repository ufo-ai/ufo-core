// The Notification app's page: a static site built with the portal's app kit. Edit this file and
// redeploy to change the page.
//
// It renders what agents have raised for the signed-in member, newest first, each row opening its
// record beside the list, and the conversations the app holds.

import {
  AppConversations,
  Badge,
  DataTable,
  Header,
  Moment,
  ObjectDetail,
  Pager,
  Panel,
  PanelEmpty,
  Section,
  SectionApp,
  Sheet,
  Td,
  TdFact,
  agentName,
  appended,
  closed,
  mountApp,
  objectAt,
  opened,
  slotOf,
  surfaceWord,
  usePanelRead,
  usePageHead,
  useState,
} from "ufo/kit";
import type { ObjectRow, Placement } from "ufo/kit";

const APP = "Notification";

const PURPOSE =
  "Decides which of the things your agents noticed are worth interrupting you for.";

const KIND = "notification";
const INBOX = "Inbox";
const NO_NOTIFICATIONS = "No notifications.";
const CONVERSATIONS = "Conversations";
const NO_CONVERSATIONS = "Your chats with this app land here.";
const COLUMNS = [
  { label: "Subject", fill: true },
  { label: "Agent", fact: true },
  { label: "Status", fact: true },
  { label: "First raised", fact: true },
];

type NotificationRow = ObjectRow & { agent_id: string };

type NotificationsPayload = {
  objects: NotificationRow[];
  next_cursor: string | null;
};

function said(value: NotificationRow[string]): string {
  return typeof value === "string" ? value : "";
}

function count(value: NotificationRow[string]): number {
  return typeof value === "number" ? value : 1;
}

function state(row: NotificationRow): string {
  const delivered = said(row.delivered_surface);
  if (delivered) return "Delivered to " + surfaceWord(delivered);
  return row.triaged === true ? "Reviewed" : "New";
}

function listPath(agentId: string, after: string | undefined): string {
  const params = new URLSearchParams({
    agent: agentId,
    order_by: "created_at",
    order: "desc",
  });
  if (after) params.set("cursor", after);
  return "/objects/" + KIND + "?" + params.toString();
}

function Home({
  agentId,
  place,
  onPlace,
}: {
  agentId: string;
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const [reloads, setReloads] = useState(0);
  const stateRead = usePanelRead<NotificationsPayload>(listPath(agentId, place.after), reloads);
  const opens = place.opens ?? [];
  const held = opens.at(-1) ?? null;
  const at = held === null ? null : objectAt(held);
  const band = usePageHead(<Header pinned heading={1} title={APP} lede={PURPOSE} />);
  return (
    <>
      {band}
      <Section title={INBOX}>
        <Panel state={stateRead}>
          {(payload) => (
            <>
              <DataTable
                columns={COLUMNS}
                rows={payload.objects}
                rowKey={(row) => row.agent_id + "/" + row.name}
                empty={NO_NOTIFICATIONS}
                open={(row) => () =>
                  onPlace({
                    ...place,
                    opens: opened(
                      opens,
                      slotOf({ agent: row.agent_id, kind: KIND, name: row.name }),
                    ),
                  })
                }
                current={(row) =>
                  held === slotOf({ agent: row.agent_id, kind: KIND, name: row.name })
                }
              >
                {(row) => (
                  <>
                    <Td className="max-w-0 text-ink">
                      <span className="flex min-w-0 items-center gap-sm">
                        <span data-part="primary" className="truncate font-medium">
                          {said(row.subject) || row.summary}
                        </span>
                        {count(row.occurrences) > 1 ? (
                          <Badge>{count(row.occurrences)} times</Badge>
                        ) : null}
                      </span>
                    </Td>
                    <TdFact>{said(row.producer) ? agentName(said(row.producer)) : "—"}</TdFact>
                    <TdFact>
                      <Badge
                        tone={
                          said(row.delivered_surface) || row.triaged === true
                            ? "default"
                            : "attention"
                        }
                      >
                        {state(row)}
                      </Badge>
                    </TdFact>
                    <TdFact>
                      {said(row.created_at) ? <Moment at={said(row.created_at)} /> : "—"}
                    </TdFact>
                  </>
                )}
              </DataTable>
              <Pager
                payload={{ older: payload.next_cursor }}
                onPlace={(next) => onPlace({ ...place, after: next.after, opens: undefined })}
              />
            </>
          )}
        </Panel>
      </Section>
      <AppConversations
        agentId={agentId}
        title={CONVERSATIONS}
        blank={NO_CONVERSATIONS}
        place={place}
        onPlace={onPlace}
      />
      {held !== null && at === null ? (
        <Sheet
          open
          title={held}
          onClose={() => onPlace({ ...place, opens: closed(opens, held) })}
        >
          <PanelEmpty>That item is not on this page.</PanelEmpty>
        </Sheet>
      ) : at === null ? null : (
        <ObjectDetail
          key={slotOf(at)}
          agentId={at.agent}
          kind={at.kind}
          name={at.name}
          onOpen={(next, aside) =>
            onPlace({
              ...place,
              opens: aside
                ? appended(opens, slotOf(next))
                : opened(opens, slotOf(next), slotOf(at)),
            })
          }
          onBack={() => {
            setReloads((value) => value + 1);
            onPlace({ ...place, opens: closed(opens, slotOf(at)) });
          }}
        />
      )}
    </>
  );
}

mountApp(document.getElementById("root")!, (init) => (
  <SectionApp
    tab="notification"
    init={init}
    view={{
      label: APP,
      remountOnPlace: false,
      ownsHeader: true,
      render: (place, onPlace) => (
        <Home agentId={init.agentId} place={place} onPlace={onPlace} />
      ),
    }}
  />
));
