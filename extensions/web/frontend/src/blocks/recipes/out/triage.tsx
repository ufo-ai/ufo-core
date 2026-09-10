import { useState } from "react";
import { IconArrowUp, IconDots, IconInbox, IconPencil, IconSearch, IconStar } from "@tabler/icons-react";

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
import { Avatar } from "@/blocks/avatar";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemFooter,
  ItemGroup,
  ItemMedia,
  ItemMeta,
  ItemSection,
  ItemTitle,
} from "@/blocks/item";
import { Menu, MenuButton, MenuCheckboxItem, MenuContent, MenuItem, MenuLabel } from "@/blocks/menu";
import { Stat } from "@/blocks/stat";

type Message = {
  id: string;
  subject: string;
  replies: string[];
  starred: boolean;
  sender?: string;
  initials?: string;
  summary?: string;
  received?: string;
};

type Data = { messages: Message[] };

type Group = { key: string; label: string; priority: boolean; empty: string };

type SortKey = { key: string; label: string; read: (message: Message) => string; latest?: boolean };

const ICON_SIZE = 16;
const ICON_STROKE = 1.5;
const SEND_MS = 900;

const GROUPS: Group[] = [
  {
    key: "priority",
    label: "Priority",
    priority: true,
    empty: "A message you mark priority appears here.",
  },
  {
    key: "rest",
    label: "Everything else",
    priority: false,
    empty: "A message appears here when it arrives.",
  },
];

const SORTS: SortKey[] = [
  { key: "date", label: "Date", read: (message) => message.received ?? "", latest: true },
  { key: "sender", label: "Sender", read: (message) => message.sender ?? "" },
  { key: "subject", label: "Subject", read: (message) => message.subject },
];

export default function App({ data }: { data: Data }) {
  const [gone, setGone] = useState<string[]>([]);
  const [priority, setPriority] = useState<Record<string, boolean>>({});
  const [held, setHeld] = useState<{ id: string; reply: string } | null>(null);
  const [draft, setDraft] = useState("");
  const [picks, setPicks] = useState(0);
  const [busy, setBusy] = useState(false);
  const [writing, setWriting] = useState(false);
  const [searching, setSearching] = useState(false);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<string | null>(null);
  const [folded, setFolded] = useState<string[]>([]);
  const [sort, setSort] = useState(SORTS[0].key);

  const waiting = data.messages.filter((message) => !gone.includes(message.id));
  const flagged = (message: Message) => priority[message.id] ?? message.starred;
  const order = SORTS.find((entry) => entry.key === sort) ?? SORTS[0];
  const needle = query.trim().toLowerCase();
  const rows = (group: Group) =>
    waiting
      .filter((message) => flagged(message) === group.priority)
      .filter((message) => `${message.sender ?? ""} ${message.subject}`.toLowerCase().includes(needle))
      .sort((one, other) => order.read(one).localeCompare(order.read(other)) * (order.latest ? -1 : 1));

  const hold = (message: Message, reply: string) => {
    setHeld({ id: message.id, reply });
    setDraft(reply);
    setPicks(picks + 1);
    setWriting(false);
  };

  const archive = (id: string) => {
    setGone([...gone, id]);
    if (held?.id !== id) return;
    setHeld(null);
    setDraft("");
  };

  const send = () => {
    if (busy || (held === null && draft.trim() === "")) return;
    const target = held?.id;
    setBusy(true);
    setTimeout(() => {
      setBusy(false);
      setHeld(null);
      setWriting(false);
      setDraft("");
      if (target) setGone((ids) => [...ids, target]);
    }, SEND_MS);
  };

  const write = () => {
    setWriting(true);
    setHeld(null);
    setDraft("");
    setPicks(picks + 1);
  };

  const toggleSearch = () => {
    setSearching(!searching);
    setQuery("");
  };

  return (
    <>
      <ActionBar variant="header">
        <ActionBarTitle
          icon={<IconInbox size={ICON_SIZE} stroke={ICON_STROKE} />}
          menu={
            <MenuContent align="start">
              <MenuLabel>Sort by</MenuLabel>
              {SORTS.map((entry) => (
                <MenuCheckboxItem
                  key={entry.key}
                  checked={entry.key === sort}
                  onCheckedChange={() => setSort(entry.key)}
                >
                  {entry.label}
                </MenuCheckboxItem>
              ))}
            </MenuContent>
          }
        >
          Triage
        </ActionBarTitle>
        <ActionBarActions>
          <IconButton label="Write" onClick={write}>
            <IconPencil size={ICON_SIZE} stroke={ICON_STROKE} />
          </IconButton>
          <IconButton label="Search" pressed={searching} onClick={toggleSearch}>
            <IconSearch size={ICON_SIZE} stroke={ICON_STROKE} />
          </IconButton>
        </ActionBarActions>
      </ActionBar>
      <Prompts wrap>
        <Prompt active={filter === null} onClick={() => setFilter(null)}>
          All
        </Prompt>
        {GROUPS.map((group) => (
          <Prompt key={group.key} active={filter === group.key} onClick={() => setFilter(group.key)}>
            {group.label}
          </Prompt>
        ))}
      </Prompts>
      {searching ? <SearchField value={query} onChange={setQuery} onClear={() => setQuery("")} /> : null}
      <Stat label="Waiting" value={String(waiting.length)} sub="Messages still in the queue" />
      <ItemGroup>
        {GROUPS.filter((group) => filter === null || filter === group.key).map((group) => {
          const shown = rows(group);
          return (
            <ItemSection
              key={group.key}
              label={group.label}
              count={shown.length}
              accent={group.priority ? "primary" : "muted"}
              open={!folded.includes(group.key)}
              onOpenChange={(open) =>
                setFolded(open ? folded.filter((key) => key !== group.key) : [...folded, group.key])
              }
              empty={group.empty}
            >
              {shown.map((message) => {
                const meta = [message.sender, message.received].filter(Boolean).join(" • ");
                const face = message.initials ?? message.sender?.slice(0, 1);
                return (
                  <Item
                    key={message.id}
                    accent={group.priority ? "primary" : "none"}
                    selected={held?.id === message.id}
                  >
                    {face ? (
                      <ItemMedia variant="avatar">
                        <Avatar alt={message.sender ?? message.subject} fallback={face} />
                      </ItemMedia>
                    ) : null}
                    <ItemContent>
                      <ItemTitle>{message.subject}</ItemTitle>
                      {message.summary ? <ItemDescription lines={2}>{message.summary}</ItemDescription> : null}
                      {meta ? <ItemMeta>{meta}</ItemMeta> : null}
                    </ItemContent>
                    <ItemActions>
                      <IconButton
                        label={`Mark ${message.subject} priority`}
                        pressed={flagged(message)}
                        onClick={() => setPriority({ ...priority, [message.id]: !flagged(message) })}
                      >
                        <IconStar size={ICON_SIZE} stroke={ICON_STROKE} />
                      </IconButton>
                      <Menu>
                        <MenuButton icon label={`More for ${message.subject}`}>
                          <IconDots size={ICON_SIZE} stroke={ICON_STROKE} />
                        </MenuButton>
                        <MenuContent>
                          <MenuItem onSelect={() => hold(message, message.replies.at(0) ?? "")}>Reply</MenuItem>
                          <MenuItem destructive onSelect={() => archive(message.id)}>
                            Archive
                          </MenuItem>
                        </MenuContent>
                      </Menu>
                    </ItemActions>
                    {message.replies.length ? (
                      <ItemFooter>
                        <Prompts wrap>
                          {message.replies.map((reply) => (
                            <Prompt
                              key={reply}
                              active={held !== null && held.id === message.id && held.reply === reply}
                              onClick={() => hold(message, reply)}
                            >
                              {reply}
                            </Prompt>
                          ))}
                        </Prompts>
                      </ItemFooter>
                    ) : null}
                  </Item>
                );
              })}
            </ItemSection>
          );
        })}
      </ItemGroup>
      {held !== null || writing ? (
        <Composer
          placeholder="Write a reply"
          value={draft}
          focusKey={picks}
          busy={busy}
          onChange={setDraft}
          onSubmit={send}
          trailing={
            <IconButton label="Send" type="submit">
              <IconArrowUp size={ICON_SIZE} stroke={ICON_STROKE} />
            </IconButton>
          }
        />
      ) : null}
    </>
  );
}
