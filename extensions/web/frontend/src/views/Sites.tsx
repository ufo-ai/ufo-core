import { useState, type FormEvent } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { Filter } from "@/components/ui/filter";
import { CardGrid } from "@/kernel/cards";
import { cn } from "@/lib/cn";
import {
  OWNER_FIELD,
  ObjectDetail,
  creator,
  type ObjectAddress,
  type ObjectRow,
} from "@/kernel/objects";
import {
  Panel,
  PanelBlank,
  PanelEmpty,
  Section,
  usePanelRead,
} from "@/kernel/panel";
import { useViewer } from "@/lib/audience";
import { useMainAgent } from "@/lib/mainAgent";

const SITE_KIND = "site";
const NOT_FOUND = 404;
const ABSENT = "No sites extension is installed.";

type SitesPayload = { objects: ObjectRow[]; next_cursor: string | null };

function visibilityLabel(visibility: string) {
  return visibility.charAt(0).toUpperCase() + visibility.slice(1);
}

export function Sites() {
  const mainAgent = useMainAgent();
  const [at, setAt] = useState<ObjectAddress | null>(null);
  if (!mainAgent)
    return <PanelEmpty>No agent answers this workspace.</PanelEmpty>;
  if (at !== null && at.name !== null) {
    return (
      <ObjectDetail
        key={at.kind + "/" + at.name}
        agentId={mainAgent.id}
        kind={at.kind}
        name={at.name}
        onOpen={(next) => setAt(next)}
        onBack={() => setAt(null)}
      />
    );
  }
  return (
    <SiteCards
      agentId={mainAgent.id}
      onOpen={(name) => setAt({ kind: SITE_KIND, name })}
    />
  );
}

function SiteCards({
  agentId,
  onOpen,
}: {
  agentId: string;
  onOpen: (name: string) => void;
}) {
  const viewer = useViewer();
  const [typed, setTyped] = useState("");
  const [query, setQuery] = useState("");
  const [mine, setMine] = useState("");
  const [cursor, setCursor] = useState("");

  const params = new URLSearchParams({ agent: agentId, order_by: "name" });
  if (query) params.set("q", query);
  if (mine) params.set("mine", "true");
  if (cursor) params.set("cursor", cursor);
  const state = usePanelRead<SitesPayload>(
    "/objects/site?" + params.toString(),
  );

  function submit(event: FormEvent) {
    event.preventDefault();
    setCursor("");
    setQuery(typed);
  }

  function changeMine(value: string) {
    setCursor("");
    setMine(value);
  }

  return (
    <Section
      title="Sites"
      bar={
        <>
          <form onSubmit={submit} className="flex items-stretch">
            <Input
              type="search"
              aria-label="Search"
              placeholder="Search"
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
              className="max-w-control-row"
            />
          </form>
          <Filter
            options={[{ label: "Mine", value: "mine" }]}
            value={mine}
            onChange={changeMine}
          />
        </>
      }
    >
      <Panel
        state={state}
        shape="cards"
        failed={(message, status) => (
          <PanelEmpty>{status === NOT_FOUND ? ABSENT : message}</PanelEmpty>
        )}
      >
        {(payload) => {
          if (!payload.objects.length)
            return query ? (
              <PanelEmpty>No sites match that search.</PanelEmpty>
            ) : (
              <PanelBlank body={mine ? "You have not created a site yet." : "No sites yet."} />
            );
          return (
            <>
              <CardGrid
                rows={payload.objects}
                rowKey={(row) => row.name}
                mark={{ shape: "band" }}
                primary={(row) => row.name}
                status={(row) =>
                  typeof row.visibility === "string"
                    ? visibilityLabel(row.visibility)
                    : null
                }
                body={(row) => (typeof row.summary === "string" ? row.summary : null)}
                meta={(row) => creator(row[OWNER_FIELD], viewer)}
                action={(row) => (
                  <div className="flex flex-wrap gap-xs">
                    {typeof row.site_url === "string" ? (
                      <a
                        href={row.site_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className={cn(buttonVariants({ variant: "send" }), "no-underline")}
                      >
                        Open
                      </a>
                    ) : null}
                    <Button variant="row" onClick={() => onOpen(row.name)}>
                      View
                    </Button>
                  </div>
                )}
              />
              {payload.next_cursor || cursor ? (
                <div className="flex gap-xs">
                  {payload.next_cursor ? (
                    <Button
                      variant="row"
                      onClick={() => setCursor(payload.next_cursor ?? "")}
                    >
                      Next page
                    </Button>
                  ) : null}
                  {cursor ? (
                    <Button variant="row" onClick={() => setCursor("")}>
                      First page
                    </Button>
                  ) : null}
                </div>
              ) : null}
            </>
          );
        }}
      </Panel>
    </Section>
  );
}
