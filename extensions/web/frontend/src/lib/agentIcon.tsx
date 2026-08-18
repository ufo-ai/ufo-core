import {
  IconAtom,
  IconBolt,
  IconBook,
  IconBrain,
  IconBriefcase,
  IconBug,
  IconBulb,
  IconCalendar,
  IconChartLine,
  IconChartPie,
  IconChecklist,
  IconClock,
  IconCloud,
  IconCode,
  IconCompass,
  IconDatabase,
  IconFlask,
  IconFolder,
  IconGavel,
  IconHeadset,
  IconLifebuoy,
  IconMail,
  IconMapPin,
  IconMessage,
  IconMicroscope,
  IconNotebook,
  IconPalette,
  IconPencil,
  IconPlane,
  IconReceipt,
  IconRobot,
  IconRocket,
  IconSearch,
  IconServer,
  IconShield,
  IconShoppingCart,
  IconSparkles,
  IconTelescope,
  IconTerminal2,
  IconUfo,
  IconUsers,
} from "@tabler/icons-react";

/** The marks an app is drawn with, and the whole set one may take: the workspace assigns from it
 *  and a member re-picks from it, so an app's mark stays a thing the eye already knows rather than
 *  a name it has to read. The order is the order the picker draws — the workspace's own mark first,
 *  then kindred marks adjacent, so the member scans groups instead of a wall of unrelated
 *  shapes. */
export const AGENT_ICONS = {
  ufo: IconUfo,
  robot: IconRobot,
  rocket: IconRocket,
  bolt: IconBolt,
  brain: IconBrain,
  sparkles: IconSparkles,
  bulb: IconBulb,
  compass: IconCompass,
  telescope: IconTelescope,
  microscope: IconMicroscope,
  flask: IconFlask,
  atom: IconAtom,
  code: IconCode,
  "terminal-2": IconTerminal2,
  bug: IconBug,
  database: IconDatabase,
  server: IconServer,
  cloud: IconCloud,
  mail: IconMail,
  message: IconMessage,
  calendar: IconCalendar,
  clock: IconClock,
  checklist: IconChecklist,
  notebook: IconNotebook,
  book: IconBook,
  folder: IconFolder,
  search: IconSearch,
  "chart-line": IconChartLine,
  "chart-pie": IconChartPie,
  receipt: IconReceipt,
  "shopping-cart": IconShoppingCart,
  users: IconUsers,
  headset: IconHeadset,
  lifebuoy: IconLifebuoy,
  shield: IconShield,
  "map-pin": IconMapPin,
  plane: IconPlane,
  briefcase: IconBriefcase,
  palette: IconPalette,
  pencil: IconPencil,
  gavel: IconGavel,
};

type AgentIconName = keyof typeof AGENT_ICONS;

/** One app's mark, drawn at the size its caller sets. It is always hidden from assistive
 *  technology: the mark restates what the row's own text and the picker's own label already say.
 *
 *  A slug outside the set raises. The set is closed at both ends — the workspace assigns from it
 *  and refuses anything else — so a slug that reaches here is the two ends disagreeing about what
 *  the set holds, and a hole where a mark belongs is a disagreement nobody reports. */
export function AgentIcon({ name, className }: { name: string; className?: string }) {
  const Drawn = AGENT_ICONS[name as AgentIconName];
  if (!Drawn) throw new Error("no app mark is drawn for " + name);
  return <Drawn className={className} aria-hidden />;
}
