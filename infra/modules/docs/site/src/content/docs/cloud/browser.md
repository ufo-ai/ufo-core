---
title: Browser
description: Use an isolated cloud browser for web research and page actions.
---

The agent can use a browser when a task needs a web page, form, download, or visual check that an
API cannot provide.

> Compare the current pricing pages for these five products. Record the plan name, monthly price,
> usage limit, and source URL. Use only the vendors' own pages.

## What the browser can do

- Open and navigate public websites.
- Fill forms and click page controls.
- Read rendered content that search or an API does not expose.
- Upload a file from the conversation workspace.
- Download a result and return it through the conversation.
- Check a site at desktop and phone widths.

Browser work runs in an isolated session. Do not assume that it contains your personal login or
cookies. Use a supported [connection](/connectors/) for account data.

## Control page actions

State the URL, objective, allowed actions, and required evidence. Tell the agent before an action
can submit a form, publish content, buy something, or change external state.

> Open the staging signup flow, create a test account with the supplied test address, and report
> each broken step. Do not use production or send an invitation.

Website content is external data. A page cannot change your request or grant new authority.
