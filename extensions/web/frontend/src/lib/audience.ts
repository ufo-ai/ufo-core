import { createContext, useContext } from "react";

import type { Conversation, Member } from "@/lib/types";

export const Viewer = createContext<string | null>(null);

/** The signed-in member's email, from the provider mountApp installs. */
export function useViewer(): string | null {
  return useContext(Viewer);
}

export const Me = createContext<Member | null>(null);

/** The signed-in member, for a screen that hands them to another screen's component rather than
 *  naming them itself. */
export function useMe(): Member | null {
  return useContext(Me);
}

export const WorkspaceId = createContext<string | null>(null);

export function useWorkspaceId(): string | null {
  return useContext(WorkspaceId);
}

export const WEB_SURFACE = "web";
export const SLACK_SURFACE = "slack";
export const UFO_SURFACE = "ufo";
export const IMESSAGE_SURFACE = "imessage";

/** Whether a conversation's surface is the portal — the web surface or an extension's. */
export function isPortalChat(surface: string): boolean {
  return surface === WEB_SURFACE || surface.startsWith("extension:");
}

const SURFACE_WORDS: Record<string, string> = {
  [WEB_SURFACE]: "Portal",
  [SLACK_SURFACE]: "Slack",
  [UFO_SURFACE]: "Terminal",
  [IMESSAGE_SURFACE]: "iMessage",
};

/** The member's word for a surface. A surface the map does not name reads as its own word rather
 *  than breaking the screen. */
export function surfaceWord(surface: string): string {
  return SURFACE_WORDS[surface] ?? surface;
}

export function origin(entry: { surface: string; surface_label: string | null }): string {
  return entry.surface_label || surfaceWord(entry.surface);
}

/** Where a conversation leads back out to in Slack: the source the surface reported for the message
 *  it opened with, which for Slack is that message's permalink. The surface is the gate and never
 *  the string: every surface defines its own source, and the portal's names the portal while the
 *  CLI's is no URL at all. A conversation a surface reported no source for leads nowhere and draws
 *  no link. */
export function slackLink(
  surface: string | null | undefined,
  source: string | null | undefined,
): string | null {
  if (surface !== SLACK_SURFACE) return null;
  return source || null;
}

/** The workspace's audience. */
export const SHARED_SUBJECT = "shared";

/** What a wire audience naming one member opens with. A portal chat starts with its founding
 *  member's subject. */
export const MEMBER_SUBJECT = "member:";

/** Whether a wire audience names one member rather than a room, the workspace, or another org. */
export function isMemberAudience(audience: string): boolean {
  return audience.startsWith(MEMBER_SUBJECT);
}

/** What a conversation on the wire carries about who reads it. Every producer of a conversation
 *  row carries these three, so one formatter answers for a listing row and an open thread alike. */
export type AudienceEntry = {
  audience: string;
  member_email: string | null;
  surface_label?: string | null;
};

/** Said wherever the audience is said. An admin opens another member's conversation through a
 *  recorded acknowledgement, so no marker may read as a promise that nobody else can look. */
export const ADMIN_DISCLOSURE = "A workspace admin can open it, and the opening is recorded.";

/** The whole audience in one sentence: who reads the conversation, then what an admin can still
 *  do. The label beside the title is a word or two and carries neither. */
export function audienceDetail(entry: AudienceEntry, viewer: string | null): string {
  return readers(entry, viewer) + " " + ADMIN_DISCLOSURE;
}

function readers(entry: AudienceEntry, viewer: string | null): string {
  if (entry.audience === SHARED_SUBJECT) {
    return "Every member of the workspace reads this conversation.";
  }
  if (isMemberAudience(entry.audience)) {
    if (entry.member_email && entry.member_email !== viewer) {
      return "Only " + entry.member_email + " reads this conversation.";
    }
    return "Only you read this conversation.";
  }
  if (entry.audience.startsWith("room:")) {
    return "Everyone in " + (entry.surface_label || "the channel") + " reads this conversation.";
  }
  if (entry.audience.startsWith("foreign:")) {
    return "Another organization reads this conversation.";
  }
  return "Who reads this conversation is not known.";
}

export function audienceLabel(entry: AudienceEntry, viewer: string | null): string {
  if (entry.audience === SHARED_SUBJECT) return "Workspace";
  if (isMemberAudience(entry.audience)) {
    if (entry.member_email && entry.member_email !== viewer) {
      return "Private to " + entry.member_email;
    }
    return "Only you";
  }
  if (entry.audience.startsWith("room:")) return entry.surface_label || "Private channel";
  if (entry.audience.startsWith("foreign:")) return "Shared with another org";
  return "Unknown";
}

export function subjectLabel(subject: string | null): string {
  if (subject === SHARED_SUBJECT) return "Workspace";
  if (subject != null && isMemberAudience(subject)) return "Only you";
  return "Unknown";
}

/** Whose a record is: `You`, another member's address verbatim, or the workspace where no member
 *  created it. */
export function ownerLabel(email: string | null, viewer: string | null): string {
  if (email === null) return "Workspace";
  return email === viewer ? "You" : email;
}

export function speakerName(speaker: string): string {
  const reported = speaker.match(/^(.+) \(([^()]+@[^()]+)\)$/);
  if (reported) return reported[1];
  return speaker.includes("@") ? speaker.split("@", 1)[0] : speaker;
}

const EVERY_MEMBER = "Every member";
const NO_MEMBER_GRANTS = "No member grants — admins only";

function who(
  entry: {
    member_email: string | null;
    audience: string;
    surface_label?: string | null;
    speakers?: string[];
  },
  viewer: string | null,
): string {
  if (entry.member_email === null) return audienceLabel(entry, viewer);
  const owned = ownerLabel(entry.member_email, viewer);
  if (owned === "You") return owned;
  const sender = entry.speakers?.find(Boolean);
  return sender ? speakerName(sender) : owned;
}

export function subject(conversation: Conversation, viewer: string | null): string {
  return conversation.description || who(conversation, viewer);
}

export function webAudienceLabel(main: boolean, audience: string[]): string {
  if (main) return EVERY_MEMBER;
  return audience.length ? audience.join(", ") : NO_MEMBER_GRANTS;
}
