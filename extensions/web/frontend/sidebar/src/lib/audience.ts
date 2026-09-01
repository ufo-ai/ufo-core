import { createContext, useContext } from "react";

/** The signed-in member's email — the address every `You` on a listing is judged against. */
export const Viewer = createContext<string | null>(null);

/** The workspace the session stands in, as the boot read stated it — the one row `workspace`-kind
 *  acts install against. */
export const WorkspaceId = createContext<string | null>(null);

export function useWorkspaceId(): string | null {
  return useContext(WorkspaceId);
}

export function useViewer(): string | null {
  return useContext(Viewer);
}

/** The names the surfaces register under, which every conversation carries as its `surface`. The
 *  CLI registers as `ufo`, and `SURFACE_WORDS` is the one place that becomes a member's word. */
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

/** The member's word for a surface. A surface the map does not name reads as its own word rather
 *  than breaking the screen. */
export function surfaceWord(surface: string): string {
  return SURFACE_WORDS[surface] ?? surface;
}

/** Where a conversation came in: the name the surface gave it (`#ops`), else the surface itself. */
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

/** The subject a record is filed under when it belongs to the whole workspace rather than to one
 *  member, one room, or another org. */
export const SHARED_SUBJECT = "shared";

/** Whether a wire audience names one member rather than a room, the workspace, or another org. */
export function isMemberAudience(audience: string): boolean {
  return audience.startsWith("member:");
}

/** Who may read a record, in the member's words. Wire audiences travel raw; this is the one map
 *  from them to what a member reads, so no view invents a second spelling. */
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

/** Who may read a memory or source, from the subject the store filed it under. The portal query
 *  already scopes member subjects to the viewer's own, so a member subject reads `Only you`. */
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

/** A speaker as a row or a bubble names one: the human name where the surface reported
 *  `Name (email)`, the local part of a bare address, else the word itself. */
export function speakerName(speaker: string): string {
  const reported = speaker.match(/^(.+) \(([^()]+@[^()]+)\)$/);
  if (reported) return reported[1];
  return speaker.includes("@") ? speaker.split("@", 1)[0] : speaker;
}

const EVERY_MEMBER = "Every member";
const NO_MEMBER_GRANTS = "No member grants — admins only";

/** Who reaches an agent on the web: every member for the main agent, else the granted addresses. */
export function webAudienceLabel(main: boolean, audience: string[]): string {
  if (main) return EVERY_MEMBER;
  return audience.length ? audience.join(", ") : NO_MEMBER_GRANTS;
}
