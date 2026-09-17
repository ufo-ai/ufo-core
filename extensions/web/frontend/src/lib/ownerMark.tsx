import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useMemberFaces } from "@/lib/memberDirectory";
import { MemberAvatar } from "@/lib/memberFace";

/** Whose a record is, wherever a listing gives that a column: the member's own picture where the
 *  workspace holds one, and one letter on one ink where it does not, because a column of tinted
 *  circles reads as a status the owner does not have. It is the circle the account row and the
 *  roster draw, so the same person is the same face on every screen. The address is under the
 *  pointer rather than in the column — one workspace fills a column with addresses that differ
 *  only before the `@`.
 *
 *  A record the read named no owner for draws nothing: the blank is the answer, and a circle over
 *  a missing address would put some other member's initial on it. */
export function OwnerMark({ name, email }: { name?: string | null; email: string | null }) {
  const faces = useMemberFaces();
  if (!email) return null;
  const face = faces(email) ?? { email, name };
  const said = face.name || email;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span role="img" aria-label={said} className="flex">
          <MemberAvatar face={face} plain />
        </span>
      </TooltipTrigger>
      <TooltipContent side="top">{said === email ? email : said + " · " + email}</TooltipContent>
    </Tooltip>
  );
}
