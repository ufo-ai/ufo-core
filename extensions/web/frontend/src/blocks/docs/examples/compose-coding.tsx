import { IconBulb, IconCode, IconDots, IconSearch, IconSparkles } from "@tabler/icons-react";

import {
  ActionBar,
  ActionBarActions,
  ActionBarTitle,
  IconButton,
  Prompt,
  Prompts,
} from "@/blocks/action-bar";
import { CodeBlock } from "@/blocks/typography";
import "@/blocks/docs/compositions.css";

const SOURCE = `"use client";

import {
  ChevronDown,
  Search,
  Sun,
  MoreHorizontal,
  Plus,
  Circle,
  GitBranch,
  ExternalLink,
} from "lucide-react";
import { useState } from "react";

type TerminalLine = {
  type: "command" | "output" | "success" | "muted";
  text: string;
};

const initialLines: TerminalLine[] = [
  { type: "command", text: "pnpm dev" },
  { type: "output", text: "" },
  { type: "output", text: "Next.js 16.0" },
  { type: "muted", text: "- Local:   http://localhost:3000" },
  { type: "muted", text: "- Network: http://192.168.1.12:3000" },
  { type: "output", text: "" },
  { type: "success", text: "Ready in 482ms" },
];

export default function CodingLane() {
  const [command, setCommand] = useState("");
  const [lines, setLines] = useState<TerminalLine[]>(initialLines);

  function runCommand() {
    const value = command.trim();

    if (!value) return;

    setLines((current) => [
      ...current,
      { type: "output", text: "" },
      { type: "command", text: value },
    ]);
  }
}`;

export default function CodingLane() {
  return (
    <div className="blk-lane">
      <ActionBar>
        <ActionBarTitle icon={<IconCode size={16} stroke={1.5} />} menu>
          Coding
        </ActionBarTitle>
        <ActionBarActions>
          <IconButton label="Search the file">
            <IconSearch size={16} stroke={1.5} />
          </IconButton>
          <IconButton label="Explain this file">
            <IconBulb size={16} stroke={1.5} />
          </IconButton>
          <IconButton label="More">
            <IconDots size={16} stroke={1.5} />
          </IconButton>
        </ActionBarActions>
      </ActionBar>
      <div className="blk-lane-body">
        <CodeBlock variant="plain" wrap>
          {SOURCE}
        </CodeBlock>
      </div>
      <div className="blk-lane-foot">
        <Prompts>
          <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>List recent todos</Prompt>
          <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>Coach me</Prompt>
          <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>Streamline calendar</Prompt>
        </Prompts>
      </div>
    </div>
  );
}
