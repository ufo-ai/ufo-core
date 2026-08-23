import { transform } from "@babel/standalone";
import * as React from "react";
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  IconChevronDown,
  IconChevronUp,
  IconDots,
  IconWorldWww,
} from "@tabler/icons-react";

import type { ReactNode } from "react";

import { connect, founded, installShims, navigate, onPlaced } from "@/apps/runtime";
import type { AppInit } from "@/apps/runtime";
import { mountApp, SectionApp, useAppLinks } from "@/apps/shell";
import type { ObjectAddress, ObjectRow } from "@/kernel/objects";
import type { Placement } from "@/kernel/pager";
import type { Face, FacetGroup } from "@/kernel/pane";
import type { PanelState } from "@/kernel/panel";
import type { ChatRow } from "@/lib/rail";
import type { Agent, Member } from "@/lib/types";
import type { PaneView } from "@/views/registry";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button, buttonVariants } from "@/components/ui/button";
import { Dialog, DialogTrigger } from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Facts, Group } from "@/components/ui/facts";
import { Segmented } from "@/components/ui/filter";
import { PressRow } from "@/components/ui/pressrow";
import { Sheet } from "@/components/ui/sheet";
import { Lede, Td, TdFact } from "@/components/ui/table";
import {
  ARTIFACT_TEXT_BYTES,
  ArtifactText,
  MediaIcon,
  isTextMedia,
  useTextArtifact,
} from "@/kernel/artifact";
import { CardGrid } from "@/kernel/cards";
import {
  HeldRecords,
  OWNER_FIELD,
  ObjectDetail,
  ObjectPane,
  creator,
  objectAt,
  slotOf,
} from "@/kernel/objects";
import { Pager } from "@/kernel/pager";
import {
  BANDS,
  COLUMN,
  FacetMenu,
  Header,
  Page,
  Pane,
  PageToolbar,
  PaneNote,
  ToolbarRule,
  ViewSwitch,
  usePageHead,
} from "@/kernel/pane";
import { Panel, PanelBlank, PanelEmpty, Section, usePanelRead } from "@/kernel/panel";
import { RebuildDialog } from "@/kernel/rebuild";
import { RowLines } from "@/kernel/rows";
import { appended, beside, closed, opened, useSlot } from "@/kernel/slots";
import { DataTable } from "@/kernel/table";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { getJson, postIntent } from "@/lib/api";
import {
  SHARED_SUBJECT,
  isMemberAudience,
  isPortalChat,
  ownerLabel,
  slackLink,
  useViewer,
} from "@/lib/audience";
import { cn } from "@/lib/cn";
import { useAgents, useMainAgent } from "@/lib/mainAgent";
import { Markdown } from "@/lib/markdown";
import { Moment, day } from "@/lib/moments";
import {
  agentHash,
  chatHash,
  conversationSlotHash,
  newChatHash,
  parseHash,
  routeIs,
  sectionHash,
  workspaceHash,
} from "@/lib/route";
import type { WorkspacePlace } from "@/lib/route";
import { formatSize } from "@/lib/size";
import { ChatPane } from "@/views/ChatPane";

import "@/theme.css";

/** The platform surface an app page composes — the frontend's `ufo.sdk`, served from the portal's
 *  own static assets and versioned with the portal deploy. An app's page is TSX in its own
 *  extension: `run` fetches it, compiles it in the browser, and executes it with this kit as its
 *  only dependency, so app code lives entirely in the extension and stays editable as the source
 *  the member's agent deploys. */

const COMPILE_PRESETS = ["typescript", ["react", { runtime: "classic" }]];

/** The page's TSX as executable JS. Types erase; JSX lands on the `React` the kit exports. */
export function compile(source: string): string {
  const compiled = transform(source, { presets: COMPILE_PRESETS, filename: "app.tsx" });
  if (typeof compiled.code !== "string") throw new Error("the page's source did not compile");
  return compiled.code;
}

/** Fetch one same-origin TSX file and execute it. The file is a script, not a module: it reads
 *  the kit off the `UfoAppKit` global and mounts itself. */
export async function run(url: string): Promise<void> {
  const answer = await fetch(url);
  if (!answer.ok) throw new Error(`the page's source did not load (${answer.status})`);
  new Function(compile(await answer.text()))();
}

export type {
  Agent,
  AppInit,
  ChatRow,
  Face,
  FacetGroup,
  Member,
  ObjectAddress,
  ObjectRow,
  PaneView,
  PanelState,
  Placement,
  ReactNode,
  WorkspacePlace,
};

export {
  React,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  IconChevronDown,
  IconChevronUp,
  IconDots,
  IconWorldWww,
  connect,
  founded,
  installShims,
  navigate,
  onPlaced,
  mountApp,
  SectionApp,
  useAppLinks,
  Avatar,
  AvatarFallback,
  Button,
  buttonVariants,
  Dialog,
  DialogTrigger,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  Facts,
  Group,
  Segmented,
  PressRow,
  Sheet,
  Lede,
  Td,
  TdFact,
  ARTIFACT_TEXT_BYTES,
  ArtifactText,
  MediaIcon,
  isTextMedia,
  useTextArtifact,
  CardGrid,
  HeldRecords,
  OWNER_FIELD,
  ObjectDetail,
  ObjectPane,
  creator,
  objectAt,
  slotOf,
  Pager,
  BANDS,
  COLUMN,
  FacetMenu,
  Header,
  Page,
  Pane,
  PageToolbar,
  PaneNote,
  ToolbarRule,
  ViewSwitch,
  usePageHead,
  Panel,
  PanelBlank,
  PanelEmpty,
  Section,
  usePanelRead,
  RebuildDialog,
  RowLines,
  appended,
  beside,
  closed,
  opened,
  useSlot,
  DataTable,
  AgentIcon,
  agentName,
  getJson,
  postIntent,
  SHARED_SUBJECT,
  isMemberAudience,
  isPortalChat,
  ownerLabel,
  slackLink,
  useViewer,
  cn,
  useAgents,
  useMainAgent,
  Markdown,
  Moment,
  day,
  /* The route table as page API: every builder it declares, the read that answers an address, and
     the test that says which kind a route is. A page compiles in the browser against this surface,
     so a builder it cannot reach is an address it spells by hand instead. */
  agentHash,
  chatHash,
  conversationSlotHash,
  newChatHash,
  parseHash,
  routeIs,
  sectionHash,
  workspaceHash,
  formatSize,
  ChatPane,
};
