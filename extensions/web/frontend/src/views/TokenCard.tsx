export function TokenCard() {
  return (
    <section className="m-auto w-card rounded-bubble border border-edge p-4xl">
      <h1 className="m-0 mb-2xs text-title">Open your workspace</h1>
      <div className="mb-xl text-label opacity-(--muted-faint)">
        Paste the session token from <code>ufoctl init</code> or your sign-in page.
      </div>
      <form method="post" action="/surface/web" className="flex gap-sm">
        <input
          name="token"
          placeholder="Session token"
          required
          autoFocus
          className="flex-1 rounded-panel border border-edge-control bg-field px-lg py-md text-field-ink"
        />
        <button
          type="submit"
          className="rounded-panel border-0 bg-ink px-3xl py-md font-strong text-surface"
        >
          Open
        </button>
      </form>
    </section>
  );
}
