import { useState } from "react";
import { IconArrowUp, IconLayoutRows, IconPlayerPause } from "@tabler/icons-react";

import {
  ActionBar,
  ActionBarActions,
  ActionBarTitle,
  Composer,
  IconButton,
  Prompt,
  Prompts,
  SearchField,
} from "@/blocks/action-bar";
import { Item, ItemContent, ItemGroup, ItemTitle } from "@/blocks/item";

const FILTERS = ["Open", "Blocked", "Shipped"];

export function ActionBarStatesInteractive() {
  const [filter, setFilter] = useState("Open");
  const [search, setSearch] = useState("");
  const [wrap, setWrap] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<string[]>([]);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, width: "100%" }}>
      <ActionBar variant="toolbar">
        <ActionBarTitle>Tasks</ActionBarTitle>
        <ActionBarActions>
          <IconButton label="Wrap the chips" active={wrap} onClick={() => setWrap(!wrap)}>
            <IconLayoutRows size={16} stroke={1.5} />
          </IconButton>
          <IconButton label="Hold the composer" active={busy} onClick={() => setBusy(!busy)}>
            <IconPlayerPause size={16} stroke={1.5} />
          </IconButton>
          <SearchField value={search} onChange={setSearch} onClear={() => setSearch("")} />
        </ActionBarActions>
      </ActionBar>
      <Prompts wrap={wrap}>
        {FILTERS.map((name) => (
          <Prompt key={name} active={filter === name} onClick={() => setFilter(name)}>
            {name}
          </Prompt>
        ))}
        <Prompt disabled>Archived</Prompt>
      </Prompts>
      <ItemGroup flush>
        {sent.map((line, at) => (
          <Item key={`${line}-${at}`} size="sm">
            <ItemContent>
              <ItemTitle>{line}</ItemTitle>
            </ItemContent>
          </Item>
        ))}
      </ItemGroup>
      <Composer
        placeholder="Ask Assistant anything..."
        busy={busy}
        trailing={
          <IconButton label="Send" type="submit">
            <IconArrowUp size={16} stroke={1.5} />
          </IconButton>
        }
        onSubmit={(text) => setSent([...sent, `${filter}: ${text}`])}
      />
    </div>
  );
}
