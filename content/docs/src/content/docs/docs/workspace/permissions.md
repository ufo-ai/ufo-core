---
title: Permissions and privacy
description: Understand workspace boundaries, agent visibility, connected accounts, memory scopes, and secret handling.
---

ufo acts through the accounts, sources, and permissions that you give it. Access to one workspace
does not give access to another workspace.

## Workspace and your UFO access

Admins can use every agent in the workspace. Other members can use workspace agents, agents they
created, and private agents shared with them.

An agent uses the connections and sources granted to it. A member speaking to your UFO
does not give it access to every account that member can use elsewhere.

## Personal and shared connections

A personal connection belongs to one member. Use it for actions performed as that person. Another
member must connect their own account when the service needs their identity.

Workspace credentials are shared service credentials. Only an admin can enter them.

## Memory and sources

Private, workspace, and room memory have separate scopes. Synced sources can also be private or
shared. Choose the narrowest scope that lets the work succeed.

An external shared channel is isolated from workspace memory in both directions. No memory or
source content crosses between customer workspaces.

## Secrets

Never put a password, key, or token in a conversation. Use the private credential control returned
by your UFO. Your UFO cannot read the stored value back or write it to a file.

## Sensitive actions

State the action limit in the request. Ask only for a diagnosis when you do not want a change.
Require approval before your UFO sends a message, publishes a document, changes production,
spends money, or changes another person's access.

> Draft the customer reply and show it to me. Do not send it.

> Diagnose the production error. Do not change configuration or deploy.
