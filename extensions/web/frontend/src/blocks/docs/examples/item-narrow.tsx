import { IconUsers } from "@tabler/icons-react";

import {
  Count,
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMeta,
  ItemTitle,
} from "@/blocks/item";

const SUMMARY = "The goal of the meeting is to align on the designs and discuss next steps for the week";

const MEETINGS = [
  { title: "Context : Name / Name", time: "2:30p • Google Meets", people: "8", past: false },
  { title: "Context : Name / Name", time: "3:15p • Google Meets", people: "6", past: false },
  { title: "Context : Name / Name", time: "2:30p • Google Meets", people: "2", past: true },
  { title: "Context : Name / Name", time: "3:30p • Google Meets", people: "2", past: true },
];

export function ItemNarrow() {
  return (
    <div style={{ width: 349 }}>
      <ItemGroup flush>
        {MEETINGS.map((meeting, index) => (
          <Item
            key={`${meeting.title}-${index}`}
            accent={meeting.past ? "muted" : "primary"}
            state={meeting.past ? "past" : "default"}
          >
            <ItemContent>
              <ItemTitle>{meeting.title}</ItemTitle>
              <ItemDescription>{SUMMARY}</ItemDescription>
              <ItemMeta>{meeting.time}</ItemMeta>
            </ItemContent>
            <ItemActions>
              <Count icon={<IconUsers size={14} stroke={1.5} />}>{meeting.people}</Count>
            </ItemActions>
          </Item>
        ))}
      </ItemGroup>
    </div>
  );
}
