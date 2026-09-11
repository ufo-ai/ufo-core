---
title: Connecting GitHub
description: Connect GitHub for repository work, issues, pull requests, and checks.
---

## Connect GitHub

Ask the agent:

> Connect my GitHub account.

Open the private authorization control. Approve access to the organizations and repositories that
you need. Each member connects their own account. The connection uses that member's GitHub access.

## What ufo can do

With the required repository access, ufo can:

- Read and change repository files.
- Clone private repositories and push branches.
- Open and update pull requests.
- Read issues, reviews, checks, and GitHub Actions runs.
- Publish requested issue, pull request, or review changes.

State the repository, branch, required proof, and publication boundary in the request.

> Fix the failing type check in `acme/web`. Run the focused tests and open a pull request. Do not
> merge it.

## Add a repository as knowledge

Use a [synced source](/work/sources/) to make Markdown files available as shared knowledge or to
start work when selected repository streams change.

## Fix GitHub access

If ufo can read issues but cannot clone or push a private repository, reconnect the account of the
member who requested the work. Confirm that this account can access the repository. A teammate's
connection does not grant your session access.

See [Writing code](/work/writing-code/) and [Reviewing pull requests](/work/reviewing-pull-requests/).
