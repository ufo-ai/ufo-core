---
title: Data ownership
description: Workspace admins can export all members' conversations, files, and memory, including private data.
---

## Admin access to exports

Workspace admins can export all members' conversations, files, and memory in the workspace.
This includes your private conversations and memory, private-agent conversations, and room
conversations. Private data is not excluded from an admin export.

Exports also include archived conversations and deleted conversations that ufo still stores.
They do not include data from another workspace or externally shared channels, such as Slack
Connect channels that include another organization.

## Export workspace data

Only admins can see **Workspace → Admin**.

1. Open **Workspace → Admin** and select **Export workspace**.
2. Read the private-data warning and select **Export all member data** to continue.
3. The status changes from **Queued** to **Preparing**, then **Ready**. You can close the page while the export runs.
4. Select **Download export** when the archive is ready.

The download is available for 24 hours after the export is ready. An expired or failed export
needs a new request. Only a current workspace admin can open the export page or request a download link.

The download is a TAR archive with numbered JSON record files and original files. Its manifest lists the
export's scope, read times, and exclusions. Credentials, connector-side content, and records or
files no longer stored by ufo are excluded.

Hosted downloads use a temporary signed link to private object storage. Anyone with that link
can download the archive until the link expires. Anyone with the archive can read its private data. Store and share the archive
securely. Access controls in ufo do not protect a downloaded copy.
