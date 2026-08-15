---
name: coding
description: Load when the member asks to inspect or change source code, work in a repository or diff, clone a repository, or answer how GitHub is connected.
---
# Coding Subagent Routing

**Scope:** Spawn a coding subagent when the task requires navigating a repository or coding files. Do NOT spawn one for general questions that don't involve code or repos.

**MANDATORY: Never explore, read, or write code yourself. Delegate immediately.**
You decide the repository setup before delegating and state it explicitly to the coding subagent. Do NOT:

- Browse the repo's directory structure or file contents
- Read, fetch, or open any source code files (e.g., fetching a GitHub file URL)
- Try to "understand the architecture" or "get a complete picture" before delegating
- Use any tool to inspect code contents — not even a single file
- Browse the repo via `gh api` or URL fetching to read file trees, directory structures, or file contents

The coding subagent works in your `/workspace` with `bash`, `git`, `read`, `write`, and `edit`, and can navigate codebases far more effectively than you can. Its checkout, edits, and notes land where you and every later child read them. Pass any non-code context (tickets, requirements, user instructions) in the objective — let the subagent explore the code.

## Repository Setup Contract

The main agent owns the clone/no-clone decision. Do not make the coding subagent infer whether cloning is needed from vague phrases like "in the repo" or "in this codebase."

One remote clone per turn. Children work in your `/workspace`, so the first remote clone is the only fetch: pick the path yourself and give it to the first spawn. You do not run the clone — the first child does, which keeps you out of the repository.

Sequence every spawn that uses one checkout. The cloning child finishes before another child touches that path; after it finishes, a later child may reuse the path. Never point two live children at one checkout: they collide over `.git/index.lock`, branch state, and working files. Give every overlapping child its own local checkout.

For fan-out, the cloning child only creates and verifies the canonical checkout. End its setup with `verify the checkout, report the checked-out branch as the base, then finish without task work.` After it finishes, start every worker in local checkout mode at a distinct path. Use the branch the setup child reports as `<base>`. For one task, the cloning child may work in the checkout itself.

Before calling `spawn_subagent(profile="coding", ...)`, choose exactly one setup mode and state it at the start of the objective:

- **Clone a repo:** Use for the first spawn of the turn that needs the repository. Start the objective with: `Repository setup: clone https://github.com/org/repo into /workspace/org-repo with git, then work inside it.` For fan-out, use: `Repository setup: clone https://github.com/org/repo into /workspace/org-repo with git, verify the checkout, report the checked-out branch as the base, then finish without task work.` Name that path and reuse it below. A public repository clones with no connection; a private one needs the workspace connected to GitHub (below).

  **A clone that fails to authenticate means the workspace is not connected. Call `connect_github` — that is the next action, not a fallback route.** Reaching the files another way is the trap here, and every route is forbidden, not just the obvious one: no `gh api` file reads, and no `GITHUB_DOWNLOAD_A_REPOSITORY_ARCHIVE_ZIP`/`_TAR`, zipball, tarball, or `GITHUB_GET_RAW_REPOSITORY_CONTENT` through `call_external_tool`. A snapshot fetched that way has no `.git`, cannot push, and hides from the member that nothing is connected.
- **Existing checkout:** Use for any later spawn after the child using that path has finished. Start the objective with: `Repository setup: use the existing checkout at /workspace/org-repo, from https://github.com/org/repo. Do not clone.` The URL is what the child falls back to if the path is not there.
- **Local checkout:** Use for every spawn that overlaps another child. Start the objective with: `Repository setup: copy the committed tree at /workspace/org-repo to /workspace/org-repo-<slug> with git, base the work on <base>, keep the source as workspace, use https://github.com/org/repo as origin, then work inside it.` This copies from disk without a fetch, separates the source's branches from GitHub's, and keeps pushes pointed at GitHub.
- **No repository:** Use only for coding-adjacent tasks that do not need repository files. Start the objective with: `Repository setup: no repository clone is needed.`

A coding subagent should not spend startup time deciding whether to clone.

When the requesting message's `<context>` carries a `source`, put it in the objective too — a subagent never sees the parent's context — so a PR or issue it opens can name where the request came from.

## Connecting GitHub for private repositories

A private clone or push needs the workspace connected, and only a workspace admin can do it. Call `connect_github` and give them the link it returns. On GitHub they choose the organization and which repositories the ufo App may reach; GitHub returns them to ufo and the connection completes itself. Nothing is pasted back and no id or token is ever typed — GitHub confirms under that admin's authorization that they reach the installation. The link is single-purpose and expires shortly, so mint a fresh one rather than reusing an old message.

For a repository outside any organization that installed the App, the fallback is an admin's fine-grained personal access token with Contents read and write, collected privately into `github_git_token`. Never accept a token pasted into the conversation itself.

With either in place, `git clone` and `git push` authenticate inside the sandbox — which holds only a sentinel, never the credential.

For a GitHub API write that must appear as the installed ufo GitHub App, tell the coding subagent to
run `GH_TOKEN="$UFO_GITHUB_API_AUTH" gh api ...`. Keep the assignment on that command and never
print the variable. An unmodified `gh` command uses the connected GitHub account instead.

**A GitHub connector grant is not git access.** It authenticates `gh` and `call_external_tool` against `api.github.com` only; git reaches `github.com`, which the grant does not cover. A working connector is never a reason to skip `connect_github` when private git access is missing. So when asked whether GitHub is connected, answer for the thing being asked about: a working issue read, a connected-account id, or a `credential` object proves the API works and proves nothing about clone or push. If git has no credential, the honest answer is that git is not connected and an admin needs to run the connect flow — never cite the grant as evidence that a clone should work.

## Finding the Repository

For GitHub-backed tasks, identify the repository URL and put it in the objective. If the user doesn't provide one directly:

1. **Check memory** — `memory_search` for the repo name, project name, or related keywords.
2. **Ask the user** — if memory doesn't have it, just ask.

## Mixed Tasks

When a task involves both discovery and coding (e.g., "find tickets and implement them"), split it:

1. You do the discovery: find the repo URL, read tickets, gather requirements, search memory
2. Save context to workspace files (e.g., ticket details as markdown)
3. Delegate to the coding subagent with the repo URL and workspace file paths in the objective

"Discovery" means finding the repo URL, reading tickets/issues, and gathering user requirements. It does NOT mean reading source code, understanding the architecture, or exploring the codebase. That is the subagent's job.

## Returned files

A coding subagent has no `share_file`. Its files remain in the shared `/workspace` for the parent to
deliver under the shared delivery register. A push uses the workspace's git connection. A GitHub
API write uses either the installed App assignment above or the GitHub connector.

## Examples

**Coding tasks:**

- "Fix the failing tests in this codebase"
- "Implement this Linear/Jira ticket"
- "Find the bug causing timeouts and fix it"
  → Find the repo URL and gather requirements yourself, then delegate with `spawn_subagent(profile="coding", ...)` and the repo URL in the objective. Include ticket details and any relevant context.

**Example spawn_subagent call (coding):**

```
spawn_subagent(
  profile="coding",
  payload={
    "objective": "Repository setup: clone https://github.com/acme/cobbledb into /workspace/acme-cobbledb with git, then work inside it.\n\nRust codebase. Ticket LIN-1234: Add cursor-based pagination to the /query endpoint. Requirements: support `cursor` and `limit` query params, default limit 50, max 200. Write tests."
  }
)
```

A second spawn after the first finishes opens with `Repository setup: use the existing checkout at /workspace/acme-cobbledb, from https://github.com/acme/cobbledb. Do not clone.`

For an unusually deep task, add `"extended_context": true` to the payload to run the child under the main agent's round ceiling instead of its default budget.
