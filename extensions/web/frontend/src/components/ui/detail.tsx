import type { ReactNode } from "react";

/** The gutter is a measure of text and does not shrink, so below the narrow breakpoint the label stands
 *  over what it labels: beside it, the prose would be squeezed to zero width. */
export function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <dl data-slot="detail" className="m-0 flex gap-lg max-narrow:flex-col max-narrow:gap-2xs">
      <dt className="w-hint shrink-0 text-label text-ink-soft max-narrow:w-auto">{label}</dt>
      <dd className="m-0 min-w-0 flex-1 text-label">{children}</dd>
    </dl>
  );
}
