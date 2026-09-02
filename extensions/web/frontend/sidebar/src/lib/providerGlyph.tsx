import {
  IconAddressBook,
  IconBrandAirtable,
  IconBrandAsana,
  IconBrandBitbucket,
  IconBrandDiscord,
  IconBrandFigma,
  IconBrandGithub,
  IconBrandGmail,
  IconBrandGoogleDrive,
  IconBrandInstagram,
  IconBrandIntercom,
  IconBrandJira,
  IconBrandMonday,
  IconBrandNotion,
  IconBrandSentry,
  IconBrandSlack,
  IconBrandStripe,
  IconBrandZoom,
  IconBrowser,
  IconBuildingBank,
  IconCalendar,
  IconCpu,
  IconCreditCard,
  IconDatabase,
  IconFileText,
  IconPlug,
  IconReportMoney,
  IconSignature,
  IconTable,
  IconUsersGroup,
  IconWallet,
  IconWorldSearch,
} from "@tabler/icons-react";

import { cn } from "@/lib/cn";

/** The mark a provider is drawn by, keyed by the slug every read names it with. */
export const PROVIDER_GLYPHS: Record<string, typeof IconPlug> = {
  airtable: IconBrandAirtable,
  apollo: IconUsersGroup,
  asana: IconBrandAsana,
  bitbucket: IconBrandBitbucket,
  attio: IconAddressBook,
  bedrock: IconCpu,
  brex: IconWallet,
  browser_use: IconBrowser,
  browserbase: IconBrowser,
  discord: IconBrandDiscord,
  docusign: IconSignature,
  exa: IconWorldSearch,
  figma: IconBrandFigma,
  github: IconBrandGithub,
  gmail: IconBrandGmail,
  googlecalendar: IconCalendar,
  googledrive: IconBrandGoogleDrive,
  googlesheets: IconTable,
  instagram: IconBrandInstagram,
  intercom: IconBrandIntercom,
  jira: IconBrandJira,
  mercury: IconBuildingBank,
  monday: IconBrandMonday,
  notion: IconBrandNotion,
  pandadoc: IconFileText,
  ramp: IconCreditCard,
  sentry: IconBrandSentry,
  slack: IconBrandSlack,
  stripe: IconBrandStripe,
  turbopuffer: IconDatabase,
  xero: IconReportMoney,
  zoom: IconBrandZoom,
};

/** The provider a row or a tile is on, drawn before the words that name it. The brokers reach far
 *  more providers than the icon set draws, so one the set does not carry takes the plain connector
 *  glyph and every option in the list still starts on the same line. */
export function ProviderGlyph({ provider, className }: { provider: string; className?: string }) {
  const Glyph = PROVIDER_GLYPHS[provider] ?? IconPlug;
  return (
    <Glyph className={cn("size-(--size-icon) shrink-0 text-ink-soft", className)} aria-hidden />
  );
}
