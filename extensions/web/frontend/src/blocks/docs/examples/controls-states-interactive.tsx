import { useState } from "react";

import { Checkbox } from "@/blocks/checkbox";
import { Item, ItemContent, ItemGroup, ItemMedia, ItemTitle } from "@/blocks/item";

const TASKS = ["Draft the migration plan", "Review the connector audit", "Rewrite the onboarding copy"];

export function ControlsStatesInteractive() {
  const [picked, setPicked] = useState<string[]>([]);
  return (
    <ItemGroup>
      <Item size="sm">
        <ItemMedia variant="checkbox">
          <Checkbox
            label="Select every task"
            checked={picked.length === TASKS.length}
            indeterminate={picked.length > 0 && picked.length < TASKS.length}
            onCheckedChange={(on) => setPicked(on ? [...TASKS] : [])}
          />
        </ItemMedia>
        <ItemContent>
          <ItemTitle>Every task</ItemTitle>
        </ItemContent>
      </Item>
      {TASKS.map((task) => (
        <Item key={task} size="sm" selected={picked.includes(task)}>
          <ItemMedia variant="checkbox">
            <Checkbox
              label={task}
              checked={picked.includes(task)}
              onCheckedChange={(on) =>
                setPicked((held) => (on ? [...held, task] : held.filter((name) => name !== task)))
              }
            />
          </ItemMedia>
          <ItemContent>
            <ItemTitle>{task}</ItemTitle>
          </ItemContent>
        </Item>
      ))}
    </ItemGroup>
  );
}
