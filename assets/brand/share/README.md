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

Neither file is hand-drawn: `render_cards.py` composes `card.html` over an
illustration from `art/` and screenshots it at exactly 1200x630. Regenerate and
verify with

```
python3 assets/brand/share/render_cards.py          # writes control/src/assets
python3 assets/brand/share/render_cards.py --check  # reproduces byte-identically
```

Chromium and Pillow are the only requirements. The brand faces are not copied
here: the script extracts the subset Canela and Inter woff2 already inlined in
`infra/modules/edge/landing.html`, so the cards can never drift from the faces
the marketing page renders.

## The type scale, and where it comes from

A link preview is rendered small — Slack, iMessage and X draw a 1200px card at
roughly 360-500px, a third of its size — so card type is set far larger than
page type. The sizes below were measured off reference cards at this exact frame
(the Granola card, and the Chutes, Opus and Notion cards in the same collection):
on the Granola card the wordmark band is 93px, the three claim lines are 85, 70
and 87px with a ~100px line pitch, and nothing on the card is smaller than a 70px
band.

| Object | Size | Share of the 630px frame |
| --- | --- | --- |
| Wordmark, Canela 300 | 52px, mark 40px | 8% |
| Headline, Canela 300 | 120-136px | 19-22% |
| Claim, Inter 500 | 46px | 7% |
| Margin, left / top | 64px / 48px | 5% of the width / 8% |

Rules that hold whatever the copy is:

- **40px floor.** Nothing smaller than 40px earns a place on the card. A 21px
  line arrives as 7px in a feed, which is a smudge.
- **Two type sizes plus the wordmark.** More hierarchy than that does not survive
  the downscale.
- **Text objects are stacked, never placed.** Every text object lives in one flex
  column with fixed gaps, so a longer headline pushes the claim down instead of
  landing on top of it.
- **The crop is measured.** `render_cards.py` finds the first row where the
  illustration's black silhouettes begin and picks the window so they start below
  the type, which is why the headline is never over a face.

## Artwork

`art/` holds the brand illustrations the cards are cut from, at 1200x1200 webp —
the size the card actually needs, since the frame is 1200 wide. `science-lab-three`
carries the ufo.ai card, `factory-kids-two` the hosted-site card. They belong to
the same set as the landing page's `bridge-command` hero: black silhouettes, ember
dots, signal-blue ground. To swap one, drop the new webp in `art/`, name it in
`CARDS` in the script, and re-render.
