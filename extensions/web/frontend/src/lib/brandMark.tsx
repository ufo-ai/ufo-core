import { cn } from "@/lib/cn";
import { ProviderGlyph } from "@/lib/providerGlyph";

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

const INK_MARKS: ReadonlySet<string> = new Set(["github", "slack"]);

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
