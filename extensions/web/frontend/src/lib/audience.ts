import { createContext, useContext } from "react";

/** The signed-in member's email — the address every `You` on a listing is judged against. */
export const Viewer = createContext<string | null>(null);

export function useViewer(): string | null {
  return useContext(Viewer);
}

const SURFACE_WORDS: Record<string, string> = { web: "Portal", slack: "Slack", cli: "CLI" };

/** The member's word for a surface. A surface the map does not name reads as its own word rather
 *  than breaking the screen. */
export function surfaceWord(surface: string): string {
  return SURFACE_WORDS[surface] ?? surface;
}

/** Where a conversation came in: the name the surface gave it (`#ops`), else the surface itself. */
export function origin(entry: { surface: string; surface_label: string | null }): string {
  return entry.surface_label || surfaceWord(entry.surface);
}

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
  if (entry.audience === "shared") return "Workspace";
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
  if (subject === "shared") return "Workspace";
  if (subject != null && isMemberAudience(subject)) return "Only you";
  return "Unknown";
}

/** Whose a record is: `You`, another member's address verbatim, or the workspace where no member
 *  created it. */
export function ownerLabel(email: string | null, viewer: string | null): string {
  if (email === null) return "Workspace";
  return email === viewer ? "You" : email;
}

const EVERY_MEMBER = "Every member";
const NO_MEMBER_GRANTS = "No member grants — admins only";

/** Who reaches an agent on the web: every member for the main agent, else the granted addresses. */
export function webAudienceLabel(main: boolean, audience: string[]): string {
  if (main) return EVERY_MEMBER;
  return audience.length ? audience.join(", ") : NO_MEMBER_GRANTS;
}
