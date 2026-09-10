import { IconUsers } from "@tabler/icons-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/blocks/card";
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

const MEETINGS = [
  {
    title: "Design sync : Ana / Priya",
    description: "Align on the designs and agree the next steps for the week",
    time: "2:30p • Google Meets",
    people: "8",
  },
  {
    title: "Roadmap review : Ana / Sam",
    description: "Walk the quarter and cut what will not land",
    time: "3:15p • Google Meets",
    people: "6",
  },
];

export default function CardWithItems() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Next up</CardTitle>
        <CardDescription>Two meetings left today.</CardDescription>
      </CardHeader>
      <CardContent>
        <ItemGroup flush>
          {MEETINGS.map((meeting) => (
            <Item key={meeting.title} accent="primary">
              <ItemContent>
                <ItemTitle>{meeting.title}</ItemTitle>
                <ItemDescription>{meeting.description}</ItemDescription>
                <ItemMeta>{meeting.time}</ItemMeta>
              </ItemContent>
              <ItemActions>
                <Count icon={<IconUsers size={14} stroke={1.5} />}>{meeting.people}</Count>
              </ItemActions>
            </Item>
          ))}
        </ItemGroup>
      </CardContent>
    </Card>
  );
}
