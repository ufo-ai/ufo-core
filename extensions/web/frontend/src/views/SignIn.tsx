import { buttonVariants } from "@/components/ui/button";
import { SIGN_IN_PATH, SIGN_OUT_PATH, type SessionFault } from "@/lib/api";
import { cn } from "@/lib/cn";

export const FAULTS: Record<
  SessionFault,
  { title: string; cause: string; action: string; door: string }
> = {
  expired: {
    title: "Session ended",
    cause: "Sign in again to open your workspace.",
    action: "Sign in",
    door: SIGN_IN_PATH,
  },
  "no-member": {
    title: "Not a member of this workspace",
    cause:
      "This workspace has no member with your email address, so signing in with it again opens nothing. An admin has to add the address, or sign in with one already on the team.",
    action: "Sign in with another email",
    door: SIGN_OUT_PATH,
  },
  "no-seat": {
    title: "Workspace access disabled",
    cause:
      "An admin disabled this address in this workspace, so signing in with it again opens nothing. Ask an admin to enable it again, or sign in with another address on the team.",
    action: "Sign in with another email",
    door: SIGN_OUT_PATH,
  },
};

export function SignIn({ fault = "expired" }: { fault?: SessionFault }) {
  const stated = FAULTS[fault];
  return (
    <section className="flex w-full max-w-form flex-col items-center gap-6xl text-center">
      <div className="flex flex-col gap-sm">
        <h1 className="m-0 text-subtitle font-medium text-ink">{stated.title}</h1>
        <p className="m-0 text-label text-ink-soft">{stated.cause}</p>
      </div>
      <div className="flex w-full max-w-(--container-connect) flex-col items-center gap-sm px-2xl">
        <a
          href={stated.door}
          className={cn(buttonVariants({ variant: "send", size: "bar" }), "h-10 w-full no-underline")}
        >
          {stated.action}
        </a>
        <p className="m-0 text-label text-ink-soft">
          On a self-hosted node, run <code className="font-mono text-mono">ufoctl portal</code> on
          the host instead.
        </p>
      </div>
    </section>
  );
}
