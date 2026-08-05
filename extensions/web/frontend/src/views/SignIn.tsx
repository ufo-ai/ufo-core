export function SignIn() {
  return (
    <section className="m-auto w-card rounded-bubble border border-edge p-4xl">
      <h1 className="m-0 mb-2xs text-title">Session ended</h1>
      <div className="mb-xl text-label opacity-(--muted-faint)">
        Sign in again to open your workspace.
      </div>
      <a
        href="/login"
        className="inline-block rounded-panel border-0 bg-ink px-3xl py-md font-strong text-surface no-underline"
      >
        Sign in
      </a>
      <div className="mt-xl text-label opacity-(--muted-faint)">
        On a self-hosted node, run <code>ufoctl portal</code> on the host instead.
      </div>
    </section>
  );
}
