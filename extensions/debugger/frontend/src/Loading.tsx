export function Loading() {
  return (
    <div role="status" className="empty loading">
      <svg aria-hidden className="loading-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <path d="M12 3a9 9 0 1 0 9 9" />
      </svg>
      <span>Loading…</span>
    </div>
  );
}
