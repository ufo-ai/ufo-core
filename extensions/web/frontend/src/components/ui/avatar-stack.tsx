import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { BRAND_MARKS, BrandMark } from "@/lib/brandMark";
import { cn } from "@/lib/cn";

export type AvatarStackPerson = {
  name: string;
  company?: string;
};

const SHOWN = 3;
const CIRCLE = "border border-card";
const OVERLAP = "-ml-2xs";
const LEADING_WWW = /^www\./;

/** No picture is asked of any host: who a workspace's members are is the workspace's, and the portal's
 *  own policy names no picture host. */
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
