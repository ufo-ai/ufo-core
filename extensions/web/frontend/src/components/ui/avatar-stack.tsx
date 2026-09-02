import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { BRAND_MARKS, BrandMark } from "@/lib/brandMark";
import { cn } from "@/lib/cn";

/** One of the members a stack is about. The name is the one thing a person cannot be drawn
 *  without: it names the stack and the initials are cut from it. The company is what turns those
 *  initials into a mark where the theme carries one, and a person without one is their initials,
 *  which is an answer rather than a gap. */
export type AvatarStackPerson = {
  name: string;
  company?: string;
};

const SHOWN = 3;
const CIRCLE = "border border-card";
const OVERLAP = "-ml-2xs";
const LEADING_WWW = /^www\./;

/** The faces of the members a row or a card is about, overlapping by the one measure the stack
 *  spells, with everyone past the third stated as a count. The overlap is a negative margin on
 *  every face after the first rather than a gap on whatever holds the stack, so a container can
 *  space the stack from its neighbours without pulling the faces apart — `ItemMedia` sets no gap
 *  for exactly this reason.
 *
 *  A member is drawn by the mark the theme carries for their company where it carries one — their
 *  real mark, vendored — and by their initials everywhere else. No picture is asked of any host: who
 *  a workspace's members are is the workspace's, and the portal's own policy names no picture host,
 *  so a face drawn from a page is drawn from what the page already holds.
 *
 *  The stack is one graphic and it names everyone, the faces the cap left out included, so a
 *  member reading by ear is told who is here once instead of hearing each set of initials spelled
 *  and a bare count after them. */
export function AvatarStack({ people }: { people: readonly AvatarStackPerson[] }) {
  if (!people.length) return null;
  const shown = people.slice(0, SHOWN);
  const rest = people.length - shown.length;
  return (
    <span
      role="img"
      aria-label={people.map((person) => person.name).join(", ")}
      data-slot="avatar-stack"
      className="flex items-center"
    >
      {shown.map((person, index) => (
        <Face key={`${index}:${person.name}`} person={person} overlapped={index > 0} />
      ))}
      {rest ? (
        <Avatar className={cn(CIRCLE, OVERLAP)}>
          <AvatarFallback aria-hidden>+{rest}</AvatarFallback>
        </Avatar>
      ) : null}
    </span>
  );
}

/** A member's face is a circle whichever way it is drawn, so a company's mark takes the round edge
 *  here rather than from the mark itself: a provider's logo stands square everywhere else. */
function Face({ person, overlapped }: { person: AvatarStackPerson; overlapped: boolean }) {
  const mark = person.company ? vendoredMark(person.company) : null;
  return (
    <Avatar title={person.name} className={cn(CIRCLE, overlapped && OVERLAP)}>
      <AvatarFallback aria-hidden>
        {mark ? (
          <BrandMark provider={mark} className="size-full rounded-full" />
        ) : (
          initials(person.name)
        )}
      </AvatarFallback>
    </Avatar>
  );
}

function vendoredMark(company: string): string | null {
  const slug = company.toLowerCase().replace(LEADING_WWW, "").split(".")[0];
  return BRAND_MARKS.has(slug) ? slug : null;
}

function initials(name: string): string {
  const words = name.trim().split(/\s+/);
  const ends = words.length > 1 ? [words[0], words[words.length - 1]] : words;
  return ends
    .map((word) => word.slice(0, 1))
    .join("")
    .toUpperCase();
}
