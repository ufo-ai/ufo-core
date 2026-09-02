import { cn } from "@/lib/cn";
import { ProviderGlyph } from "@/lib/providerGlyph";

/** The providers the portal draws in their own colours, keyed by the slug every read names them
 *  with. The theme declares one `--brand-<slug>` per entry, so this is exactly the set the
 *  stylesheet can answer. */
export const BRAND_MARKS: ReadonlySet<string> = new Set([
  "airtable",
  "anthropic",
  "apollo",
  "asana",
  "attio",
  "basecamp",
  "box",
  "cal",
  "calendly",
  "canva",
  "capsule_crm",
  "clickup",
  "contentful",
  "crowdin",
  "dart",
  "datadog",
  "dialpad",
  "discord",
  "discordbot",
  "dropbox",
  "dub",
  "eventbrite",
  "excel",
  "exist",
  "facebook",
  "fathom",
  "figma",
  "freeagent",
  "github",
  "gitlab",
  "gmail",
  "gong",
  "google_analytics",
  "google_maps",
  "googlecalendar",
  "googledrive",
  "googlemeet",
  "googlesheets",
  "googleslides",
  "gumroad",
  "harvest",
  "hubspot",
  "hugging_face",
  "instagram",
  "kit",
  "linear",
  "linkedin",
  "linkhut",
  "mailchimp",
  "mcp",
  "microsoft_teams",
  "miro",
  "monday",
  "moneybird",
  "notion",
  "one_drive",
  "openai",
  "openrouter",
  "outlook",
  "pagerduty",
  "perplexity",
  "prisma",
  "productboard",
  "pushbullet",
  "reddit",
  "reddit_ads",
  "roam",
  "salesforce",
  "sentry",
  "share_point",
  "shippo",
  "slack",
  "slackbot",
  "splitwise",
  "stack_exchange",
  "strava",
  "stripe",
  "supabase",
  "ticketmaster",
  "ticktick",
  "timely",
  "todoist",
  "toneden",
  "trello",
  "typeform",
  "wakatime",
  "webex",
  "whatsapp",
  "wrike",
  "youtube",
  "zendesk",
  "zeplin",
  "zoho",
  "zoho_bigin",
  "zoom",
]);

/** The marks the theme declares a second time for a filled act's ground. Only a provider with an
 *  act of its own needs one, so this set is smaller than the vendored set, and a slug outside it
 *  draws on the pane's token wherever it stands. */
const INK_MARKS: ReadonlySet<string> = new Set(["github", "slack"]);

/** A provider's mark where the portal offers it, drawn as the square the theme hands over: the art
 *  is a brand's own picture and a round edge would cut it, so the mark takes no mask of its own and
 *  whatever holds it owns the shape. The slug is data, so the token it names resolves through the
 *  `style` object rather than through a class.
 *
 *  A provider with no vendored mark takes its glyph at the same square, so a grid of tiles holds
 *  one rhythm whichever it draws.
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
