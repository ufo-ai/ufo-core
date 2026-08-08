import { buttonVariants } from "@/components/ui/button";
import type { SessionFault } from "@/lib/api";
import { cn } from "@/lib/cn";

const FAULTS: Record<SessionFault, { title: string; cause: string; action: string }> = {
  expired: {
    title: "Session ended",
    cause: "Sign in again to open your workspace.",
    action: "Sign in",
  },
  "no-member": {
    title: "Not a member of this workspace",
    cause:
      "This workspace has no member with your email address, so signing in with it again opens nothing. An admin has to add the address, or sign in with one already on the team.",
    action: "Sign in with another email",
  },
};

export function SignIn({ fault = "expired" }: { fault?: SessionFault }) {
  const stated = FAULTS[fault];
  return (
    <section className="m-auto w-card rounded-card border border-edge p-4xl">
      <h1 className="m-0 mb-2xs text-title">{stated.title}</h1>
      <div className="mb-xl text-label opacity-(--muted-faint)">{stated.cause}</div>
      <a
        href="/login"
        className={cn(buttonVariants({ variant: "send" }), "inline-block no-underline")}
      >
        {stated.action}
      </a>
      <div className="mt-xl text-label opacity-(--muted-faint)">
        On a self-hosted node, run <code>ufoctl portal</code> on the host instead.
      </div>
    </section>
  );
}
