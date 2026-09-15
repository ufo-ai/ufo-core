import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/** Whose a record is, wherever a listing gives that a column: one letter, because a second initial
 *  is a name the portal does not hold, and one ink, because a column of tinted circles reads as a
 *  status the owner does not have. The address is under the pointer rather than in the column —
 *  one workspace fills a column with addresses that differ only before the `@`.
 *
 *  A record the read named no owner for draws nothing: the blank is the answer, and a circle over
 *  a missing address would put some other member's initial on it. */
export function OwnerMark({ name, email }: { name?: string | null; email: string | null }) {
  if (!email) return null;
  const said = name || email;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Avatar aria-label={said}>
          <AvatarFallback>{said.slice(0, 1).toUpperCase()}</AvatarFallback>
        </Avatar>
      </TooltipTrigger>
      <TooltipContent side="top">{name ? name + " · " + email : email}</TooltipContent>
    </Tooltip>
  );
}
