---
name: ufo-style
description: "Load when the member asks for ufo's house colours, type, spacing, radius, or other exact visual-system facts."
---
# House style

The portal's own theme is the house style. Anything built for the product or for the workspace
itself takes these tokens: an application's homepage, an internal page or dashboard, a deck or a
chart the team reads. Draw from the tokens; do not invent a second palette beside them.

## When the house style is the default, and when it is not

| The build | The style |
| --- | --- |
| An application's homepage, an internal page, a dashboard, a workspace deck or chart | the house style, always |
| The member named a palette, a font, a brand, or a reference to match | theirs, whole — never a mix of the two |
| A site with an audience and a subject of its own (a public site the member is publishing) | art direction from the subject, as `website-building` step 1 orders |

The member's own direction outranks this skill wherever they gave one, including a direction given
mid-build. Say in one line which style you took and why, so a member who wanted their own can say
so before the build is finished.

## A page built on the app kit

An application's homepage is built against `ufo/kit`, and the kit is where the house style already
is: its components carry these tokens, both colour schemes and every width, so a page composed from
them is in the style before it states anything. `read` `references/kit.md` — every component the kit
publishes and what each one is, written from the kit itself — and reach for one before building a
shape out of `div`s. A measure is a `Stat`, a state a `Badge`, a unit a member acts on a `Card`, a
named share a `Breakdown`, a series a `Chart`, and a graphic in more than one colour carries a
`Legend`.

Such a page takes no stylesheet of its own: the tokens below are for a build that has no kit — a
deck, a document, a public site.

## The tokens

`read` `references/tokens.css`. It is the copy-ready implementation: the eight palette declarations,
the roles drawn from them, the type scale, the spacing ramp, the radii, the easings, and the one
shadow. Copy it and `assets/fonts/` into the project with the same relative layout, import the
stylesheet first, and name only its variables afterwards — a raw hex or a raw measurement in the
page is the drift this skill exists to stop. Never fetch a house font from the network.

Paths are relative to this skill's directory (`$UFO_HOME/skills/ufo-style/`).

## What the tokens commit you to

- **Colour.** Three background steps, two text steps, the soft mark step, two accents.
  `light-dark()` carries both schemes off one declaration, so write no dark-mode variant of your own.
  Every other colour is one step or a mix of two.
- **A word takes a text step, a mark takes the mark step.** Secondary words are set in
  `--color-ink-soft`, which is the derived AA-safe pair (`#676767` light, `#A7A9A9` dark).
  `--color-mark-soft` carries `#919090` for a hairline, a rule or a dot — it measures 3.0:1 on
  the light surface, so no word is ever set in it.
- **Type.** Inter for text, Roboto Mono for code, Georgia for display, as `tokens.css` writes them.
  The portal's own display face is licensed to the portal alone: it is not in this stack, and a build
  of ours never adds it or loads it with an `@font-face` rule.
- **Size.** The scale is fixed-px chrome type, 11-24px, for app-like screens, plus one display step
  — `--text-display` (32px) — for the screen's own title, used once per screen. Nothing else on an
  app screen goes above `--text-title`. A reading or landing page keeps the fluid scale
  `website-building` teaches and takes the palette from here.
- **Chrome.** The product supplies the page's header and everything in the top right — theme,
  settings, account. A screen of ours adds no bar that crosses the centre of the page and puts no
  control in that corner; it starts at its own title.
- **Shape and motion.** One radius (`0.25rem`) for a control and a panel, `--radius-card` for a
  card or a plot, and a pill for a row.  Motion
  is short and functional: `--ease-control` for a control, the enter/leave pair for anything that
  arrives over the page.

## A deck, a PDF, a chart

No CSS variable resolves in those, so take the hexes out of `references/tokens.css` and write them
literally. `design-foundations` carries this same palette per medium — the type sizes a format asks
for, the chart sequence, and every pairing that may carry a word — so when that skill is in the load
read its references rather than deriving any of it again.

## Before you hand it over

- No colour that is not a token or a mix of two tokens.
- Text clears WCAG AA: 4.5:1 at body size, 3:1 at 24px or at 18.66px bold. `--color-ink` clears it on
  every background step. An accent is a fill, not a word: a link is set in `--color-link`, and a
  label on top of an accent fill in `--color-fill-ink`.
- Measure the secondary pair in the light scheme and state the ratio you measured. It is the pair
  that fails: `--color-ink-soft` on `--color-surface` reads 5.4:1 and passes, the mark step in the
  same place reads 3.0:1 and does not, so measure the rendered text rather than trusting the role
  name. The dark scheme clears AA on every step (`#A7A9A9` on `#191A1A` is 7.4:1).
- Both schemes checked. The palette answers a scheme on its own, but a screenshot proves it.
- The narrow width checked at 360px, not only the desktop width: `document.scrollWidth` equals the
  viewport and no element is clipped. This is required before handover, not a suggestion.
