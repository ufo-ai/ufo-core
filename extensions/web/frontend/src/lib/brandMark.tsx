import { cn } from "@/lib/cn";
import { ProviderGlyph } from "@/lib/providerGlyph";

/** The providers the portal draws in their own colours, keyed by the slug every read names them
 *  with. The theme declares one `--brand-<slug>` per entry, so this is exactly the set the
 *  stylesheet can answer. */
export const BRAND_MARKS: ReadonlySet<string> = new Set([
  "asana",
  "attio",
  "discord",
  "figma",
  "github",
  "gmail",
  "googlecalendar",
  "googledrive",
  "googlesheets",
  "linear",
  "notion",
  "slack",
  "stripe",
  "zoom",
]);

/** The marks the theme declares a second time for a filled act's ground. Only a provider with an
 *  act of its own needs one, so this set is smaller than the vendored set, and a slug outside it
 *  draws on the pane's token wherever it stands. */
const INK_MARKS: ReadonlySet<string> = new Set(["github", "slack"]);

/** A provider's mark where the portal offers it. The slug is data, so the token it names resolves
 *  through the `style` object rather than through a class; a provider with no vendored mark takes
 *  its glyph at the same square, so a grid of tiles holds one rhythm whichever it draws.
 *
 *  `onInk` draws the mark for a filled act instead of for the pane. That act is ink on the pane, so
 *  its ground is the scheme's other end: a mark of one ink reads on exactly one of the two, and the
 *  one it reads on is not the button. */
export function BrandMark({
  provider,
  onInk,
  className,
}: {
  provider: string;
  onInk?: boolean;
  className?: string;
}) {
  if (!BRAND_MARKS.has(provider)) {
    const glyph = cn("size-(--size-brand-mark)", className);
    return <ProviderGlyph provider={provider} className={glyph} />;
  }
  const ground = onInk && INK_MARKS.has(provider) ? "-on-ink" : "";
  return (
    <span
      className={cn(
        "block size-(--size-brand-mark) shrink-0 bg-contain bg-center bg-no-repeat",
        className,
      )}
      style={{ backgroundImage: `var(--brand-${provider}${ground})` }}
      aria-hidden
    />
  );
}
