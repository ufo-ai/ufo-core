import {
  AVATAR_GROUP_SHOWN,
  Avatar,
  AvatarFallback,
  AvatarGroup,
  AvatarGroupCount,
} from "@/components/ui/avatar";
import { BRAND_MARKS, BrandMark } from "@/lib/brandMark";
import { FaceCircle, photoAddress } from "@/lib/memberFace";

export type AvatarStackPerson = {
  name: string;
  company?: string;
  email?: string | null;
  photo_url?: string | null;
};

const LEADING_WWW = /^www\./;

/** The faces of the members a row or a card is about, overlapping by the one measure the stack
 *  spells, with everyone past the third stated as a count. That measure is the `2xs` the group
 *  draws every run of marks at, and it is a negative margin on every face after the first rather
 *  than a gap on whatever holds the stack, so a container can space the stack from its neighbours
 *  without pulling the faces apart — `ItemMedia` sets no gap for exactly this reason.
 *
 *  A member is drawn by the mark the theme carries for their company where it carries one — their
 *  real mark, vendored — then by the picture this workspace holds for them, and by their initials
 *  where it holds none. No picture is asked of another host: the portal serves a member's stored
 *  picture from its own origin, and its policy names no picture host.
 *
 *  The stack is one graphic and it names everyone, the faces the cap left out included, so a
 *  member reading by ear is told who is here once instead of hearing each set of initials spelled
 *  and a bare count after them. */
export function AvatarStack({ people }: { people: readonly AvatarStackPerson[] }) {
  if (!people.length) return null;
  const shown = people.slice(0, AVATAR_GROUP_SHOWN);
  const rest = people.length - shown.length;
  return (
    <AvatarGroup slot="avatar-stack" label={people.map((person) => person.name).join(", ")}>
      {shown.map((person, index) => (
        <Face key={`${index}:${person.name}`} person={person} />
      ))}
      {rest ? <AvatarGroupCount count={rest} round /> : null}
    </AvatarGroup>
  );
}

function Face({ person }: { person: AvatarStackPerson }) {
  const mark = person.company ? vendoredMark(person.company) : null;
  if (mark) {
    return (
      <Avatar stacked title={person.name}>
        <AvatarFallback aria-hidden>
          <BrandMark provider={mark} className="size-full rounded-full" />
        </AvatarFallback>
      </Avatar>
    );
  }
  return (
    <FaceCircle
      name={person.name}
      photo={photoAddress(person.photo_url)}
      tint={person.email ?? person.name}
      title={person.name}
      stacked
    />
  );
}

function vendoredMark(company: string): string | null {
  const slug = company.toLowerCase().replace(LEADING_WWW, "").split(".")[0];
  return BRAND_MARKS.has(slug) ? slug : null;
}

