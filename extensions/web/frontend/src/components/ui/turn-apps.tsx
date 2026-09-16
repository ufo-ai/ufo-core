import { Fragment } from "react";

import { IconChevronRight } from "@tabler/icons-react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  Item,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemSeparator,
  ItemTitle,
} from "@/components/ui/item";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { cn } from "@/lib/cn";
import { agentHash } from "@/lib/route";
import type { ChatApp } from "@/lib/types";

export function TurnApps({ apps }: { apps: ChatApp[] }) {
  return (
    <ItemGroup className="mt-lg max-w-bubble">
      {apps.map((app, index) => (
        <Fragment key={app.id}>
          {index ? <ItemSeparator /> : null}
          <Item size="flush">
            <a
              href={agentHash(app.id)}
              className={cn(
                "flex min-w-0 flex-1 items-center gap-lg rounded-panel px-xl py-lg",
                "text-inherit no-underline hover:bg-fill",
              )}
            >
              <Avatar>
                <AvatarFallback>
                  <AgentIcon name={app.icon} />
                </AvatarFallback>
              </Avatar>
              <ItemContent>
                <ItemTitle>{agentName(app.name)}</ItemTitle>
                <ItemDescription>{app.model}</ItemDescription>
              </ItemContent>
              <IconChevronRight aria-hidden className="size-icon shrink-0 text-ink-soft" />
            </a>
          </Item>
        </Fragment>
      ))}
    </ItemGroup>
  );
}
