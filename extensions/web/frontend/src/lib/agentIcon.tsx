import { useEffect, useState } from "react";
import { IconQuestionMark } from "@tabler/icons-react";

import brandMark from "@brand/ufo-mark.svg";

import { cn } from "@/lib/cn";
import {
  Acanthus,
  Adyton,
  Akhet,
  Anthemion,
  Ashnan,
  Atef,
  Aten,
  Carnyx,
  Cedrus,
  Deltoton,
  Denticulus,
  Dingir,
  Flabellum,
  Furcula,
  Gibil,
  Gnomon,
  Gorgoneion,
  Hydria,
  Kalyx,
  Kardia,
  Krepis,
  Kylix,
  Lekythos,
  Menhir,
  Nabatu,
  Nephele,
  Nirah,
  Nochtli,
  Omphalos,
  Osculum,
  Ostrakon,
  Patera,
  Propylon,
  Sesen,
  Shushan,
  Stele,
  Thyrsus,
  Triglyph,
  Wedjat,
  Ziggurat,
} from "@/lib/elementIcons";

declare const __MARK_SPRITES__: Record<string, string>;

const RESERVED_MARK = "ufo";

export const AGENT_ICONS = {
  propylon: Propylon,
  nabatu: Nabatu,
  gibil: Gibil,
  adyton: Adyton,
  dingir: Dingir,
  akhet: Akhet,
  deltoton: Deltoton,
  aten: Aten,
  omphalos: Omphalos,
  lekythos: Lekythos,
  anthemion: Anthemion,
  stele: Stele,
  nirah: Nirah,
  nochtli: Nochtli,
  ziggurat: Ziggurat,
  menhir: Menhir,
  nephele: Nephele,
  carnyx: Carnyx,
  osculum: Osculum,
  denticulus: Denticulus,
  gnomon: Gnomon,
  triglyph: Triglyph,
  acanthus: Acanthus,
  ostrakon: Ostrakon,
  hydria: Hydria,
  wedjat: Wedjat,
  krepis: Krepis,
  patera: Patera,
  ashnan: Ashnan,
  kylix: Kylix,
  furcula: Furcula,
  kalyx: Kalyx,
  kardia: Kardia,
  gorgoneion: Gorgoneion,
  thyrsus: Thyrsus,
  flabellum: Flabellum,
  cedrus: Cedrus,
  sesen: Sesen,
  shushan: Shushan,
  atef: Atef,
};

type AgentIconName = keyof typeof AGENT_ICONS;
type MarkPath = { d: string; fill?: string; stroke?: string; opacity?: string };

const GLYPH = "size-(--size-glyph)";
const MARK_NAME = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;

/** One app's mark, drawn at `--size-glyph` unless its caller sets another size, in the ink of
 *  whatever it sits in. It is always hidden from assistive technology: the mark restates what the
 *  row's own text and the picker's own label already say.
 *
 *  The picker's marks are the element pack's own paths, bundled and drawn at once, and the reserved
 *  mark is the brand's own file; any other slug tabler draws is read from its letter's sprite, one
 *  fetch per letter for the page's life. A slug no sprite answers is reported to the console and
 *  drawn as the unknown mark, so the hole is visible and named while the row it sits in still lists
 *  its app. */
export function AgentIcon({ name, className }: { name: string; className?: string }) {
  if (!MARK_NAME.test(name)) return <UnknownMark name={name} className={className} />;
  if (name === RESERVED_MARK) return <BrandMark className={className} />;
  if (Object.hasOwn(AGENT_ICONS, name)) {
    const Drawn = AGENT_ICONS[name as AgentIconName];
    return <Drawn className={cn(GLYPH, className)} aria-hidden />;
  }
  return <FetchedMark name={name} className={className} />;
}

function BrandMark({ className }: { className?: string }) {
  return (
    <span
      className={cn("brand-mark block shrink-0 bg-current", GLYPH, className)}
      style={{ mask: `url(${brandMark}) center / contain no-repeat` }}
      aria-hidden
    />
  );
}

const marks = new Map<string, Map<string, MarkPath[]>>();
const reading = new Map<string, Promise<void>>();

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

function readSprite(letter: string): Promise<void> {
  const held = reading.get(letter);
  if (held) return held;
  const asked = fetchSprite(letter).then((read) => {
    // A read that answered nothing is not held: one refused fetch would otherwise draw every mark under
    // its letter as unknown for the life of the page.
    if (read.size) marks.set(letter, read);
    else reading.delete(letter);
  });
  reading.set(letter, asked);
  return asked;
}

async function fetchSprite(letter: string): Promise<Map<string, MarkPath[]>> {
  const read = new Map<string, MarkPath[]>();
  const file = __MARK_SPRITES__[letter];
  if (!file) return read;
  const answer = await fetch(file).catch(() => null);
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
