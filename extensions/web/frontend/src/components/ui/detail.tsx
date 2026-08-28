import type { ReactNode } from "react";

/** One aspect of a subject stated at length: the aspect named in a gutter on the left, what is
 *  true of it set beside that gutter and wrapped. It is the shape a page takes when the answer is
 *  prose — the cadence a report runs on, what a meeting settled — where `Facts` is the shape a
 *  pane takes when the answer is a value. The two are not interchangeable: a fact holds one pitch
 *  and cuts its value with an ellipsis so the column stays scannable, and an aspect the member
 *  came to read has to be allowed to grow instead.
 *
 *  The gutter is one width for every aspect on a page, so the sentences start on one edge the eye
 *  runs down. The pair is a description list rather than two boxes, because a label and what it
 *  labels are one relation and a reader that builds pairs out of the markup should find it.
 *
 *  Below the narrow breakpoint the label stands over what it labels instead of beside it. The
 *  gutter is a measure of text and does not shrink, so on a phone it is wider than the whole row
 *  and the prose beside it is squeezed to nothing — present, readable by ear, and drawn zero
 *  pixels wide. A page's aspects are the page, so that is the whole screen gone. */
export function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <dl data-slot="detail" className="m-0 flex gap-lg max-narrow:flex-col max-narrow:gap-2xs">
      <dt className="w-hint shrink-0 text-label text-ink-soft max-narrow:w-auto">{label}</dt>
      <dd className="m-0 min-w-0 flex-1 text-label">{children}</dd>
    </dl>
  );
}
