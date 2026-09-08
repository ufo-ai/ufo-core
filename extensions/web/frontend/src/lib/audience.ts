import { createContext, useContext } from "react";

export const Viewer = createContext<string | null>(null);

export function useViewer(): string | null {
  return useContext(Viewer);
}

export const WorkspaceId = createContext<string | null>(null);

export function useWorkspaceId(): string | null {
  return useContext(WorkspaceId);
}

export const WEB_SURFACE = "web";
export const SLACK_SURFACE = "slack";
export const UFO_SURFACE = "ufo";
export const IMESSAGE_SURFACE = "imessage";

export function isPortalChat(surface: string): boolean {
  return surface === WEB_SURFACE || surface.startsWith("extension:");
}

const SURFACE_WORDS: Record<string, string> = {
  [WEB_SURFACE]: "Portal",
  [SLACK_SURFACE]: "Slack",
  [UFO_SURFACE]: "Terminal",
  [IMESSAGE_SURFACE]: "iMessage",
};

export function surfaceWord(surface: string): string {
  return SURFACE_WORDS[surface] ?? surface;
}

export function origin(entry: { surface: string; surface_label: string | null }): string {
  return entry.surface_label || surfaceWord(entry.surface);
}

export function slackLink(
  surface: string | null | undefined,
  source: string | null | undefined,
): string | null {
  if (surface !== SLACK_SURFACE) return null;
  return source || null;
}

export const SHARED_SUBJECT = "shared";

export function isMemberAudience(audience: string): boolean {
  return audience.startsWith("member:");
}

export function audienceLabel(
  entry: { audience: string; member_email: string | null; surface_label?: string | null },
  viewer: string | null,
): string {
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

export function webAudienceLabel(main: boolean, audience: string[]): string {
  if (main) return EVERY_MEMBER;
  return audience.length ? audience.join(", ") : NO_MEMBER_GRANTS;
}
