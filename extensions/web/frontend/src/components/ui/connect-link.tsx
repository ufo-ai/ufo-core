import { IconCheck } from "@tabler/icons-react";

import { buttonVariants } from "@/components/ui/button";
import { BASE } from "@/lib/api";
import { BrandMark } from "@/lib/brandMark";
import { cn } from "@/lib/cn";
import { ConsentLink } from "@/lib/consent";
import type { ChatConnect } from "@/lib/types";

export function ConnectLink({ connect }: { connect: ChatConnect }) {
  const mark = connect.provider ? (
    <BrandMark provider={connect.provider} className="size-icon" />
  ) : null;
  const named = connect.label ?? "account";
  if (!connect.turn) {
    return (
      <span
        className={cn(
          buttonVariants({ variant: "outline", size: "bar" }),
          "self-start text-ink-soft",
        )}
      >
        {mark}
        {named + " connected"}
        {connect.account ? <span className="truncate">{connect.account}</span> : null}
        <IconCheck className="size-icon text-ink" aria-hidden />
      </span>
    );
  }
  // The address the press opens is this surface's own: it mints the consent URL for this turn's request
  // and redirects the window there, so the chip holds nothing that can go stale.
  return (
    <ConsentLink
      url={BASE + "/turns/" + connect.turn + "/connect"}
      className={cn(
        buttonVariants({ variant: "outline", size: "bar" }),
        "self-start no-underline",
      )}
    >
      {mark}
      {connect.label ? "Connect " + connect.label : "Connect account"}
    </ConsentLink>
  );
}
