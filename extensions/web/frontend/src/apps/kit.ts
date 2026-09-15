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
  IconFilter2,
  IconWorldWww,
  IconX,
} from "@tabler/icons-react";

import type { MouseEvent as ReactMouseEvent, ReactNode, RefObject } from "react";

import { ApplicationAction } from "@/apps/action";
import type { ApplicationActionRecord } from "@/apps/action";
import { AppConversations } from "@/apps/bands";
import {
  compose,
  connect,
  founded,
  installShims,
  navigate,
  onPlaced,
} from "@/apps/runtime";
import type { AppInit } from "@/apps/runtime";
import { mountApp, SectionApp, useAppLinks } from "@/apps/shell";
import type { ObjectAddress, ObjectRow } from "@/kernel/objects";
import type { Placement } from "@/kernel/pager";
import type { Face, FacetGroup } from "@/kernel/pane";
import type { PanelState } from "@/kernel/panel";
import type { ChatRow } from "@/lib/rail";
import type { Crumb } from "@/lib/title";
import type { Agent, Conversation, Member } from "@/lib/types";
import type { PaneView } from "@/views/registry";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { AvatarStack } from "@/components/ui/avatar-stack";
import type { AvatarStackPerson } from "@/components/ui/avatar-stack";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Breakdown,
  BreakdownHeader,
  BreakdownLabel,
  BreakdownMark,
  BreakdownName,
  BreakdownRow,
  BreakdownRows,
  BreakdownValue,
} from "@/components/ui/breakdown";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Chart, ChartBars } from "@/components/ui/chart";
import type { ChartBar } from "@/components/ui/chart";
import { Detail } from "@/components/ui/detail";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Facts, Group } from "@/components/ui/facts";
import { Legend, LegendItem } from "@/components/ui/legend";
import { Meter } from "@/components/ui/meter";
import type { MeterPart } from "@/components/ui/meter";
import { Separator } from "@/components/ui/separator";
import { Segmented } from "@/components/ui/filter";
import { SurfaceGlyph } from "@/lib/surfaceMark";
import { PressRow } from "@/components/ui/pressrow";
import { Sheet } from "@/components/ui/sheet";
import {
  Stat,
  StatDelta,
  StatDescription,
  StatHeader,
  StatLabel,
  StatMedia,
  StatValue,
} from "@/components/ui/stat";
import { Clip, Lede, Td, TdFact, TdWhole } from "@/components/ui/table";
import {
  ARTIFACT_TEXT_BYTES,
  ArtifactText,
  FileSheet,
  MediaIcon,
  isTextMedia,
  useTextArtifact,
} from "@/kernel/artifact";
import type { SharedFile } from "@/kernel/artifact";
import { CardGrid } from "@/kernel/cards";
import {
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
import {
  Empty,
  Panel,
  PanelBlank,
  PanelEmpty,
  Section,
  Loading,
  usePanelRead,
} from "@/kernel/panel";
import { RebuildDialog } from "@/kernel/rebuild";
import { RowLines } from "@/kernel/rows";
import { appended, beside, closed, opened } from "@/kernel/slots";
import { DataTable } from "@/kernel/table";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { BrandMark } from "@/lib/brandMark";
import { getJson, postIntent, postObjectAction } from "@/lib/api";
import {
  IMESSAGE_SURFACE,
  SHARED_SUBJECT,
  SLACK_SURFACE,
  UFO_SURFACE,
  isMemberAudience,
  isPortalChat,
  ownerLabel,
  slackLink,
  surfaceWord,
  useViewer,
} from "@/lib/audience";
import { cn } from "@/lib/cn";
import { useAgents, useMainAgent } from "@/lib/mainAgent";
import { Markdown } from "@/lib/markdown";
import { Moment, day } from "@/lib/moments";
import {
  agentHash,
  agentsHash,
  agentSetupHash,
  chatHash,
  chatsHash,
  conversationSlotHash,
  firstRunHash,
  homeHash,
  newChatHash,
  parseHash,
  routeIs,
  sectionHash,
  automationsHash,
  workspaceHash,
} from "@/lib/route";
import type { WorkspacePlace } from "@/lib/route";
import { formatSize } from "@/lib/size";
import { FoundingChat } from "@/views/Chat";
import { ChatPane } from "@/views/ChatPane";
import { ConversationDetail } from "@/views/Conversations";

import "@/theme.css";

/* `jsxImportSource` names this module for the element types too, and a page whose tags resolve to
   nothing typechecks as `any`. */
export { Fragment, jsx, jsxs } from "react/jsx-runtime";
export type { JSX } from "react/jsx-runtime";

export type {
  Agent,
  ApplicationActionRecord,
  AppInit,
  AvatarStackPerson,
  ChartBar,
  MeterPart,
  ChatRow,
  Conversation,
  Crumb,
  Face,
  FacetGroup,
  Member,
  ObjectAddress,
  ObjectRow,
  PaneView,
  PanelState,
  Placement,
  ReactMouseEvent,
  ReactNode,
  RefObject,
  SharedFile,
  WorkspacePlace,
};

export {
  AppConversations,
  compose,
  React,
  ApplicationAction,
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
  IconFilter2,
  IconWorldWww,
  IconX,
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
  AvatarStack,
  Badge,
  Breakdown,
  BreakdownHeader,
  BreakdownLabel,
  BreakdownMark,
  BreakdownName,
  BreakdownRow,
  BreakdownRows,
  BreakdownValue,
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
  Chart,
  ChartBars,
  Button,
  buttonVariants,
  Detail,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  Facts,
  Group,
  Legend,
  LegendItem,
  Meter,
  Segmented,
  Separator,
  PressRow,
  Sheet,
  Stat,
  StatDelta,
  StatDescription,
  StatHeader,
  StatLabel,
  StatMedia,
  StatValue,
  Clip,
  Lede,
  Td,
  TdFact,
  TdWhole,
  ARTIFACT_TEXT_BYTES,
  ArtifactText,
  FileSheet,
  MediaIcon,
  isTextMedia,
  useTextArtifact,
  CardGrid,
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
  Empty,
  Panel,
  PanelBlank,
  PanelEmpty,
  Section,
  Loading,
  usePanelRead,
  RebuildDialog,
  RowLines,
  appended,
  beside,
  closed,
  opened,
  DataTable,
  AgentIcon,
  agentName,
  BrandMark,
  getJson,
  postIntent,
  postObjectAction,
  IMESSAGE_SURFACE,
  SHARED_SUBJECT,
  SLACK_SURFACE,
  UFO_SURFACE,
  isMemberAudience,
  isPortalChat,
  ownerLabel,
  slackLink,
  SurfaceGlyph,
  surfaceWord,
  useViewer,
  cn,
  useAgents,
  useMainAgent,
  Markdown,
  Moment,
  day,
  agentHash,
  agentsHash,
  agentSetupHash,
  chatHash,
  chatsHash,
  conversationSlotHash,
  firstRunHash,
  homeHash,
  newChatHash,
  parseHash,
  routeIs,
  sectionHash,
  automationsHash,
  workspaceHash,
  formatSize,
  ChatPane,
  FoundingChat,
  ConversationDetail,
};
