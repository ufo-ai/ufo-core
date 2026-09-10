import { useState } from "react";
import { IconUsers } from "@tabler/icons-react";

import {
  Count,
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemHeader,
  ItemMeta,
  ItemTitle,
} from "@/blocks/item";

const TODAY = [
  { title: "Context : Name / Name", time: "2:30p • Google Meets", people: "8" },
  { title: "Design review", time: "3:15p • Google Meets", people: "6" },
  { title: "Weekly planning", time: "4:00p • Google Meets", people: "9" },
] as const;

const YESTERDAY = [
  { title: "Context : Name / Name", time: "2:30p • Google Meets", people: "2" },
  { title: "Connector handover", time: "3:30p • Google Meets", people: "2" },
] as const;

const SUMMARY = "The goal of the meeting is to align on the designs and discuss next steps for the week";

export function ItemMeeting() {
  const [today, setToday] = useState(true);
  const [yesterday, setYesterday] = useState(true);
  return (
    <ItemGroup>
      <ItemHeader badge="26" accent="primary" collapsible open={today} onOpenChange={setToday}>
        Today
      </ItemHeader>
      {today
        ? TODAY.map((meeting) => (
            <Item key={meeting.title} accent="primary">
              <ItemContent>
                <ItemTitle>{meeting.title}</ItemTitle>
                <ItemDescription>{SUMMARY}</ItemDescription>
                <ItemMeta>{meeting.time}</ItemMeta>
              </ItemContent>
              <ItemActions>
                <Count icon={<IconUsers size={14} stroke={1.5} />}>{meeting.people}</Count>
              </ItemActions>
            </Item>
          ))
        : null}
      <ItemHeader badge="25" collapsible open={yesterday} onOpenChange={setYesterday}>
        Yesterday
      </ItemHeader>
      {yesterday
        ? YESTERDAY.map((meeting) => (
            <Item key={meeting.title} accent="muted" state="past">
              <ItemContent>
                <ItemTitle>{meeting.title}</ItemTitle>
                <ItemDescription>{SUMMARY}</ItemDescription>
                <ItemMeta>{meeting.time}</ItemMeta>
              </ItemContent>
              <ItemActions>
                <Count icon={<IconUsers size={14} stroke={1.5} />}>{meeting.people}</Count>
              </ItemActions>
            </Item>
          ))
        : null}
    </ItemGroup>
  );
}
