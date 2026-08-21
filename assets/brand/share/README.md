# Share cards

`og-home.jpg` is the `og:image` for ufo.ai; `og-site.jpg` is the one a hosted
site's frame page points at. Both are 1200x630 progressive JPEG, served
anonymously and immutably from the apex at `/share/og-home.jpg` and
`/share/og-site.jpg` — the gateway compiles both in (`control/src/web.rs`), so an
unfurler that carries no session still gets them.

This directory holds the artboard, the artwork and the renderer; the two renders
themselves are committed at `control/src/assets/og-home.jpg` and
`control/src/assets/og-site.jpg`, because the gateway image builds from the
`control` tree alone and reaches nothing above it.

Neither file is hand-drawn: `render_cards.py` composes `card.html` over the flat
ground, draws the logo lockup from `art/` on it, and screenshots it at exactly
1200x630. Regenerate and verify with

```
python3 assets/brand/share/render_cards.py          # writes control/src/assets
python3 assets/brand/share/render_cards.py --check  # reproduces byte-identically
```

Chromium and Pillow are the only requirements. The brand faces are not copied
here: the script extracts the three subset woff2 faces already inlined in
`infra/modules/edge/landing.html` — Canela 300, its true italic, and Inter 500 —
so the cards can never drift from the faces the marketing page renders. A card
sets Canela, the brand's display face. It is deliberately not the face of the
wordmark inside the lockup art, which is Albertus Nova: a card's words are
display copy, so they take the display face, and the wordmark arrives as artwork.

## The geometry, and where it comes from

Both cards are the logo lockup on the flat ground and nothing else; the
hosted-site card adds one line of type above it. The spacing is the source art's
own: the lockup SVG is a 1184x555 canvas that already carries the reference
padding, so `card.html` places that canvas at a set width and measures no margin
of its own. Measured on the committed renders:

| Object | Measured on the render |
| --- | --- |
| Lockup ink, `og-home` | 813 x 203 at x190..1002, y213..415, 68% of the frame width |
| Field left / right / above / below, `og-home` | 190px / 197px / 213px / 214px |
| Line, Canela 300 at 108px, `og-site` | a 412 x 86 ink band at x394..805, y167..252 |
| Lockup ink, `og-site` | 601 x 150 at x299..899, from y312 |
| Line, gap and lockup as one group, `og-site` | 296px tall, a 59px gap, 167px of field above and 168px below |

Rules that hold whatever the copy is:

- **40px floor.** Nothing smaller than 40px earns a place on the card. A link
  preview is rendered small — Slack, iMessage and X draw a 1200px card at roughly
  360-500px, a third of its size — so a 21px line arrives as 7px, which is a
  smudge.
- **One line at most, and it sits above the lockup.** More hierarchy than that
  does not survive the downscale, and the lockup is what the card is for.
- **The line is one size on every card.** 108px is the size the first
  hosted-site card set it at. A card that carries a line shrinks the lockup to
  suit rather than resizing the line, so the words read the same on any card.
- **The group is centred, not placed.** The line and the lockup hold equal field
  above and below, and the gap between them stays far smaller than that field, so
  the pair reads as one object. Their ink centres sit within a pixel of each
  other, at x599.5 and x599.0, which is what the line's 3px optical offset buys:
  the italic M opens with a wide left sidebearing and pulls a centred line left.
- **Nothing reflows.** Every object is placed at coordinates measured off the
  render, so a longer line does not push the lockup down — it grows into it.
  Change the copy, re-render, and measure the new render.

## Artwork

`art/logo-ember-with-padding.svg` is the lockup both cards draw: three ember dots
(`#FFD18B`) left of the white wordmark, drawn as outlined paths on a transparent
1184x555 canvas. It is committed unedited, because the padding inside its canvas
is the card's spacing. To change the words above the lockup, edit `CARDS` in the
script — it maps each card's stem to the one line set above the lockup, and
`<em>` takes the true italic. To swap the art, drop the new SVG in `art/`, name
it in `LOCKUP`, and re-render.

`art/` also still holds `science-lab-three.webp` and `factory-kids-two.webp`, the
brand illustrations the earlier cards were cut from. No card draws them now.
