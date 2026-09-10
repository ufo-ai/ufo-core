import {
  IconChevronRight,
  IconCopy,
  IconDots,
  IconHistory,
  IconMicrophone,
  IconPlus,
} from "@tabler/icons-react";

import {
  ActionBar,
  ActionBarActions,
  ActionBarTitle,
  Composer,
  IconButton,
} from "@/blocks/action-bar";
import { Avatar } from "@/blocks/avatar";
import { Card, CardContent } from "@/blocks/card";
import { Prose } from "@/blocks/typography";
import "@/blocks/docs/compositions.css";

const ASSISTANT: [string, string] = ["#a150e1", "#ee8c29"];

export default function AssistantLane() {
  return (
    <div className="blk-lane">
      <ActionBar>
        <ActionBarTitle icon={<Avatar alt="Assistant" gradient={ASSISTANT} />} menu>
          Assistant
        </ActionBarTitle>
        <ActionBarActions>
          <IconButton label="New conversation">
            <IconPlus size={16} stroke={1.5} />
          </IconButton>
          <IconButton label="History">
            <IconHistory size={16} stroke={1.5} />
          </IconButton>
          <IconButton label="More">
            <IconDots size={16} stroke={1.5} />
          </IconButton>
        </ActionBarActions>
      </ActionBar>
      <div className="blk-lane-body">
        <Prose width="full">
          <p>
            You have four apps open. Ask for anything you want done in them and I will take it from
            there.
          </p>
          <p>
            I also put together a competitive analysis of the business and where the time is going.
          </p>
        </Prose>
        <Card variant="pill" align="start">
          <Avatar alt="Assistant" gradient={ASSISTANT} />
          <span>Competitive Analysis</span>
          <IconChevronRight size={16} stroke={1.5} />
        </Card>
        <ActionBarActions>
          <IconButton label="Copy the reply">
            <IconCopy size={16} stroke={1.5} />
          </IconButton>
          <IconButton label="More">
            <IconDots size={16} stroke={1.5} />
          </IconButton>
        </ActionBarActions>
        <Card variant="muted" size="sm" inline align="end" style={{ maxWidth: "72%" }}>
          <CardContent>
            <p>Read it back to me after the design sync.</p>
          </CardContent>
        </Card>
      </div>
      <div className="blk-lane-foot">
        <Composer
          rows={2}
          placeholder="Ask Assistant anything..."
          leading={
            <IconButton label="Attach a file">
              <IconPlus size={16} stroke={1.5} />
            </IconButton>
          }
          trailing={
            <IconButton label="Dictate">
              <IconMicrophone size={16} stroke={1.5} />
            </IconButton>
          }
        />
      </div>
    </div>
  );
}
