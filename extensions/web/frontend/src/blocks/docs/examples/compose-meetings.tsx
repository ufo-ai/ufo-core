import { useState } from "react";
import {
  IconCalendar,
  IconCalendarShare,
  IconDots,
  IconPlus,
  IconSearch,
  IconSparkles,
  IconUsers,
} from "@tabler/icons-react";

import {
  ActionBar,
  ActionBarActions,
  ActionBarTitle,
  IconButton,
  Prompt,
  Prompts,
} from "@/blocks/action-bar";
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
import { Prose } from "@/blocks/typography";
import "@/blocks/docs/compositions.css";

const SUMMARY =
  "The goal of the meeting is to align on the designs and discuss next steps for the week";

const TODAY = [
  { title: "Design sync : Ana / Priya", time: "2:30p • Google Meets", people: "8" },
  { title: "Roadmap review : Ana / Sam", time: "3:15p • Google Meets", people: "6" },
  { title: "Weekly planning : Team", time: "4:00p • Google Meets", people: "9" },
];

const YESTERDAY = [
  { title: "Connector handover : Sam / Ada", time: "2:30p • Google Meets", people: "2" },
  { title: "Support triage : Ada / Ravi", time: "3:00p • Google Meets", people: "2" },
  { title: "Pricing review : Ana / Ravi", time: "3:30p • Google Meets", people: "4" },
  { title: "Import walkthrough : Sam / Priya", time: "4:15p • Google Meets", people: "3" },
];

function DayPrompts() {
  return (
    <Prompts>
      <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>List recent todos</Prompt>
      <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>Coach me</Prompt>
      <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>Streamline calendar</Prompt>
    </Prompts>
  );
}

export default function MeetingsLane() {
  const [today, setToday] = useState(true);
  const [yesterday, setYesterday] = useState(true);
  return (
    <div className="blk-lane">
      <ActionBar>
        <ActionBarTitle icon={<IconCalendar size={16} stroke={1.5} />} menu>
          Meetings
        </ActionBarTitle>
        <ActionBarActions>
          <IconButton label="New meeting">
            <IconPlus size={16} stroke={1.5} />
          </IconButton>
          <IconButton label="Share calendar">
            <IconCalendarShare size={16} stroke={1.5} />
          </IconButton>
          <IconButton label="Search meetings">
            <IconSearch size={16} stroke={1.5} />
          </IconButton>
          <IconButton label="More">
            <IconDots size={16} stroke={1.5} />
          </IconButton>
        </ActionBarActions>
      </ActionBar>
      <div className="blk-lane-body">
        <ItemGroup>
          <ItemHeader badge="26" accent="primary" collapsible open={today} onOpenChange={setToday}>
            Today
          </ItemHeader>
          {today ? (
            <>
              <Prose width="full">
                <p>
                  Here is a summary of today&apos;s design sync. We covered a lot of ground — from
                  the new navigation pattern to component library updates and a few outstanding
                  questions that need engineering input before we can move forward.
                </p>
              </Prose>
              <DayPrompts />
              {TODAY.map((meeting) => (
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
              ))}
            </>
          ) : null}
          <ItemHeader badge="25" collapsible open={yesterday} onOpenChange={setYesterday}>
            Yesterday
          </ItemHeader>
          {yesterday ? (
            <>
              <DayPrompts />
              {YESTERDAY.map((meeting) => (
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
              ))}
            </>
          ) : null}
        </ItemGroup>
      </div>
    </div>
  );
}
