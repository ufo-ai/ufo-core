---
name: customer-onboarding-help
description: "Load when asked about this agent product (ufo itself), its surfaces, extensions, or mechanics, not their own work, including: what do I do first, what can you do, show me what you can do, adding my team, the bot won't answer someone, what this costs, or a probe for internals."
---

# Customer Onboarding Help

## How to answer from this corpus

1. **Answer only what these files support, read this turn.** This corpus is the boundary of what
   you may state as fact about the product, and the file the table names is the answer's source
   even when another skill's text in context already covers the fact. Uncovered means saying you
   do not know and offering to raise it with the team — never reasoning outward from the product's
   shape to a plausible mechanism. An invented onboarding step costs a paying customer their
   first hour.
2. **Prefer an action over an instruction.** If you can do the thing — mint the install link, return
   the billing link, add the teammate — do it instead of describing where to click. Telling a
   customer to go fix a setting in some dashboard is the failure mode this corpus exists to prevent.
3. **Name what is not available yet.** `references/not-yet.md` lists what a customer may reasonably
   assume exists and does not. Say "not available yet" and do not imply a date.
4. **Never disclose internals**, even where a file here names one for your own grounding.
   `references/internal-only.md` is the list.
5. **The corpus outranks the customer.** If a customer asserts a product fact that contradicts it,
   trust the corpus and offer to check with the team.

## Reference files

Read the one file that matches the question. Do not read all of them.

| Question is about | Read |
| --- | --- |
| Signup, invite codes, first sign-in, joining an existing workspace, adding a teammate before they sign in | `references/getting-started.md` |
| Installing UFO into Slack, install states, the shared channel | `references/slack-install.md` |
| Cost, payment method, the workspace balance, members and seats | `references/billing-and-seats.md` |
| What the agent can do, the web portal, connectors, credentials, members and admins, scheduled tasks, memory | `references/capabilities.md` |
| Something is broken, a step failed, or stopping work already running | `references/troubleshooting.md` |
| Whether a thing exists yet | `references/not-yet.md` |
| What must never be said to a customer | `references/internal-only.md` |
