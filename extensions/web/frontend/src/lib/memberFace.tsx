import type { CSSProperties } from "react";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { BASE } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { MemberFace } from "@/lib/types";

export const MEMBER_TINTS = [
  "bg-member-1 text-member-1-ink",
  "bg-member-2 text-member-2-ink",
  "bg-member-3 text-member-3-ink",
];

const FNV_OFFSET = 2166136261;
const FNV_PRIME = 16777619;

/** Off the top bits: `% 6` over FNV-1a's low ones put two addresses at one company on one colour,
 *  and `Math.abs` folds two hashes onto one number at the sign bit. */
function memberTint(key: string): string {
  let hash = FNV_OFFSET;
  for (const character of key.toLowerCase()) {
    hash = Math.imul(hash ^ character.codePointAt(0)!, FNV_PRIME);
  }
  const spread = (hash >>> 0) / 2 ** 32;
  return MEMBER_TINTS[Math.floor(spread * MEMBER_TINTS.length)];
}

/** One letter, because a second initial is a name the portal does not hold. */
export function initialsOf(name: string): string {
  return name.trim().slice(0, 1).toUpperCase();
}

/** The name to print for a member. The wire carries the drawn name already, so this answers only
 *  for a row that carries none. */
export function faceName(face: MemberFace): string {
  return face.name || face.email.split("@", 1)[0] || face.email;
}

/** The address the portal serves a stored picture from, or null where the wire named none. Every
 *  payload carries the path relative to the surface, so the origin is spelled here once rather than
 *  at each caller — an app page draws the portal framed on its own origin, where a path named
 *  without one resolves against the site rather than the route serving it. */
export function photoAddress(photoUrl: string | null | undefined): string | null {
  return photoUrl ? BASE + "/" + photoUrl : null;
}

/** The one circle a person is drawn in, wherever the portal draws one: the chat header over their
 *  words, the roster row, the chat list's owner, the account menu, the stack on a card. Their
 *  picture where they have one, their initial on a colour the `tint` key fixes where they do not —
 *  so the same person is the same circle on every screen, and two people in one conversation are
 *  told apart before either name is read.
 *
 *  `tint` is the member's address wherever the caller holds one, because a person's name changes
 *  and their address does not; a speaker this workspace holds no row for falls back to their name.
 *  An app's mark and a company's are not this — they draw the bare `Avatar`, which carries no
 *  person's colour. */
export function FaceCircle({
  name,
  photo,
  tint,
  title,
  className,
  style,
}: {
  name: string;
  photo: string | null;
  tint: string;
  title?: string;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <Avatar title={title} className={className} style={style}>
      {photo ? <AvatarImage src={photo} alt="" /> : null}
      <AvatarFallback aria-hidden className={cn("font-medium", memberTint(tint))}>
        {initialsOf(name)}
      </AvatarFallback>
    </Avatar>
  );
}

export function MemberAvatar({ face, className }: { face: MemberFace; className?: string }) {
  return (
    <FaceCircle
      name={faceName(face)}
      photo={photoAddress(face.photo_url)}
      tint={face.email}
      className={className}
    />
  );
}

/** A member's face beside their name, for a row that says who somebody is. The name is text, not a
 *  label on the picture, so a reader hears it once: the circle is decoration beside words that
 *  already name the member. */
export function MemberNameplate({ face, className }: { face: MemberFace; className?: string }) {
  return (
    <span className={cn("flex min-w-0 items-center gap-2xs", className)}>
      <MemberAvatar face={face} className="size-(--size-glyph)" />
      <span className="truncate">{faceName(face)}</span>
    </span>
  );
}
