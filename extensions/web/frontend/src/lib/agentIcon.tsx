import { useEffect, useState } from "react";
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
  IconQuestionMark,
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

import { cn } from "@/lib/cn";

/** The sprite each opening letter's marks are drawn in, by letter, under the hashed name
 *  `vite.config.ts` cut and emitted it as. */
declare const __MARK_SPRITES__: Record<string, string>;

/** The marks the picker offers, in the order it draws them: the workspace's own mark first, then
 *  kindred marks adjacent, so the member scans groups instead of a wall of unrelated shapes. The
 *  set a mark may come from is every outline mark tabler draws — this is the finite, ordered part
 *  of it a member chooses from by eye, and the part the bundle carries. */
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
type MarkPath = { d: string; fill?: string; stroke?: string; opacity?: string };

const GLYPH = "size-(--size-glyph)";
/** The shape of every name tabler draws a mark under. A name is matched against this before it is
 *  looked up, so a slug that happens to name something every object carries — `constructor`,
 *  `valueOf` — is a name no mark answers rather than a lookup that finds one. */
const MARK_NAME = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;

/** One app's mark, drawn at `--size-glyph` unless its caller sets another size, in the ink of
 *  whatever it sits in. It is always hidden from assistive technology: the mark restates what the
 *  row's own text and the picker's own label already say.
 *
 *  The picker's marks are bundled and draw at once; any other slug tabler draws is read from its
 *  letter's sprite, one fetch per letter for the page's life. A slug no sprite answers is reported
 *  to the console and drawn as the unknown mark, so the hole is visible and named while the row it
 *  sits in still lists its app. */
export function AgentIcon({ name, className }: { name: string; className?: string }) {
  if (!MARK_NAME.test(name)) return <UnknownMark name={name} className={className} />;
  if (Object.hasOwn(AGENT_ICONS, name)) {
    const Drawn = AGENT_ICONS[name as AgentIconName];
    return <Drawn className={cn(GLYPH, className)} aria-hidden />;
  }
  return <FetchedMark name={name} className={className} />;
}

const marks = new Map<string, Map<string, MarkPath[]>>();
const reading = new Map<string, Promise<void>>();

/** A mark read from its letter's sprite, drawn as the bundled marks are drawn: `currentColor`
 *  strokes it, so it takes the ink of the row, the menu item or the picker cell around it. Until
 *  the sprite is read the mark is an empty box of the same size, so nothing beside it moves when
 *  the paths arrive. */
function FetchedMark({ name, className }: { name: string; className?: string }) {
  const letter = name.slice(0, 1);
  const [, redraw] = useState(0);
  useEffect(() => {
    if (marks.has(letter)) return;
    let live = true;
    readSprite(letter).then(() => {
      if (live) redraw((at) => at + 1);
    });
    return () => {
      live = false;
    };
  }, [letter]);
  const read = marks.get(letter);
  if (!read) return <svg viewBox="0 0 24 24" className={cn(GLYPH, className)} aria-hidden />;
  const paths = read.get(name);
  if (!paths) return <UnknownMark name={name} className={className} />;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={24}
      height={24}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn("tabler-icon", `tabler-icon-${name}`, GLYPH, className)}
      aria-hidden
    >
      {paths.map((path, at) => (
        <path key={at} d={path.d} fill={path.fill} stroke={path.stroke} opacity={path.opacity} />
      ))}
    </svg>
  );
}

/** Every mark one letter's sprite draws, by slug, read once for the life of the page: the fetch
 *  is held here, so the second mark under a letter draws from what the first read. */
function readSprite(letter: string): Promise<void> {
  const held = reading.get(letter);
  if (held) return held;
  const asked = fetchSprite(letter).then((read) => {
    // A read that answered nothing is not held: one refused fetch would otherwise draw every mark
    // under its letter as unknown for the life of the page.
    if (read.size) marks.set(letter, read);
    else reading.delete(letter);
  });
  reading.set(letter, asked);
  return asked;
}

/** A sprite the surface cannot answer reads as no marks rather than rejecting: the caller reports
 *  the slug it could not draw and draws the unknown mark, which is what keeps a row whose mark is
 *  missing a row the member can still read and open. */
async function fetchSprite(letter: string): Promise<Map<string, MarkPath[]>> {
  const read = new Map<string, MarkPath[]>();
  const file = __MARK_SPRITES__[letter];
  if (!file) return read;
  const answer = await fetch(import.meta.env.BASE_URL + file).catch(() => null);
  if (!answer?.ok) {
    console.error("no marks were served for " + letter);
    return read;
  }
  const held = new DOMParser().parseFromString(await answer.text(), "image/svg+xml");
  for (const symbol of held.querySelectorAll("symbol")) {
    const paths = [...symbol.querySelectorAll("path")].map((path) => ({
      d: path.getAttribute("d") ?? "",
      fill: path.getAttribute("fill") ?? undefined,
      stroke: path.getAttribute("stroke") ?? undefined,
      opacity: path.getAttribute("opacity") ?? undefined,
    }));
    read.set(symbol.getAttribute("id") ?? "", paths);
  }
  return read;
}

const reported = new Set<string>();

function UnknownMark({ name, className }: { name: string; className?: string }) {
  if (!reported.has(name)) {
    reported.add(name);
    console.error("no app mark is drawn for " + name);
  }
  return <IconQuestionMark className={cn(GLYPH, className)} aria-hidden />;
}
