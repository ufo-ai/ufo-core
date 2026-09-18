import { IconChevronRight } from "@tabler/icons-react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Card } from "@/components/ui/card";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemLink,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { agentHash } from "@/lib/route";
import type { ChatApp } from "@/lib/types";
import { modelLabel } from "@/lib/models";

export function TurnApps({ apps }: { apps: ChatApp[] }) {
  return (
    <div className="mt-sm flex max-w-said flex-col gap-sm">
      {apps.map((app) => (
        <Card key={app.id} rows>
          <ItemGroup>
            <Item size="flush">
              <ItemLink href={agentHash(app.id)}>
                <ItemMedia>
                  <Avatar>
                    <AvatarFallback>
                      <AgentIcon name={app.icon} />
                    </AvatarFallback>
                  </Avatar>
                </ItemMedia>
                <ItemContent>
                  <ItemTitle>{agentName(app.name)}</ItemTitle>
                  <ItemDescription>{modelLabel(app.model)}</ItemDescription>
                </ItemContent>
                <ItemActions>
                  <IconChevronRight aria-hidden className="size-icon shrink-0 text-ink-soft" />
                </ItemActions>
              </ItemLink>
            </Item>
          </ItemGroup>
        </Card>
      ))}
    </div>
  );
}
