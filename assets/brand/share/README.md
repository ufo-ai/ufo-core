# Share cards

The 1200x630 cards every link unfurler draws: `og-home.jpg` for `https://ufo.ai/`, `og-site.jpg` for
a hosted ufo site's frame page. Both are generated, so neither is edited by hand. This directory
holds the artboard and the renderer; the two renders themselves are committed at
`control/src/assets/og-home.jpg` and `control/src/assets/og-site.jpg`, because the gateway image
builds from the `control` tree alone and reaches nothing above it.

`card.html` is the artboard. It draws nothing of its own: `render_cards.py` extracts the hero
artwork and the three subset brand faces out of `infra/modules/edge/landing.html`, so the cards carry
the marketing page's own illustration and typography and no second copy of either can drift.
Chromium renders the artboard at exactly 1200x630 and Pillow writes a progressive JPEG at q88.

Regenerate both files:

```sh
python3 assets/brand/share/render_cards.py
```

It writes both renders into `control/src/assets`. The output is byte-identical for the same page, so
a run with no change to the marketing page leaves the committed files untouched. Needs headless
`chromium` and Pillow.

The gateway compiles both files in and serves them at `/share/og-home.jpg` and `/share/og-site.jpg`
(`control/src/web.rs`), which is the one anonymous, immutable image origin on the apex — an unfurler
carries no session, so nothing behind one can be an `og:image`.
