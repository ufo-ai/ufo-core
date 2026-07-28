---
name: coding
description: Load before working in a source tree — code files (.py, .ts, .go, etc), a repo, or a diff, and before a first clone or any GitHub-connection answer. E.g. trace how something works across files; implement a described change; find and fix a failing test; clone a private repo.
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

The coding subagent works in its own sandbox with `bash`, `git`, `read`, `write`, and `edit`, and can navigate codebases far more effectively than you can. Pass any non-code context (tickets, requirements, user instructions) in the objective — let the subagent explore the code.

## Repository Setup Contract

The main agent owns the clone/no-clone decision. Do not make the coding subagent infer whether cloning is needed from vague phrases like "in the repo" or "in this codebase."

Before calling `spawn_subagent(profile="coding", ...)`, choose exactly one setup mode and state it at the start of the objective:

- **Clone a repo:** Use when the task targets a GitHub repository. Start the objective with: `Repository setup: clone https://github.com/org/repo into the workspace with git, then work inside it.` Public repositories always clone; private ones need the workspace connected to GitHub (below).

  **A clone that fails to authenticate means the workspace is not connected. Call `connect_github` — that is the next action, not a fallback route.** Reaching the files another way is the trap here, and every route is forbidden, not just the obvious one: no `gh api` file reads, and no `GITHUB_DOWNLOAD_A_REPOSITORY_ARCHIVE_ZIP`/`_TAR`, zipball, tarball, or `GITHUB_GET_RAW_REPOSITORY_CONTENT` through `call_external_tool`. A snapshot fetched that way has no `.git`, cannot push, and hides from the member that nothing is connected.
- **Existing workspace:** Use when the repository is already present in the sandbox workspace. Start the objective with: `Repository setup: use the existing workspace at <path>. Do not clone.`
- **No repository:** Use only for coding-adjacent tasks that do not need repository files. Start the objective with: `Repository setup: no repository clone is needed.`

A coding subagent should not spend startup time deciding whether to clone.

## Connecting GitHub for private repositories

A private clone or push needs the workspace connected, and only a workspace admin can do it. Call `connect_github` and give them the link it returns. On GitHub they choose the organization and which repositories the ufo App may reach; GitHub returns them to ufo and the connection completes itself. Nothing is pasted back and no id or token is ever typed — GitHub confirms under that admin's authorization that they reach the installation. The link is single-purpose and expires shortly, so mint a fresh one rather than reusing an old message.

For a repository outside any organization that installed the App, the fallback is an admin's fine-grained personal access token with Contents read and write, collected privately into `github_git_token`. Never accept a token pasted into the conversation itself.

With either in place, `git clone` and `git push` authenticate inside the sandbox — which holds only a sentinel, never the credential.

**A GitHub connector grant is not git access.** It authenticates `gh` and `call_external_tool` against `api.github.com` only; git reaches `github.com`, which the grant does not cover. So when asked whether GitHub is connected, answer for the thing being asked about: a working issue read, a connected-account id, or a `credential` object proves the API works and proves nothing about clone or push. If git has no credential, the honest answer is that git is not connected and an admin needs to run the connect flow — never cite the grant as evidence that a clone should work.

## Finding the Repository

For GitHub-backed tasks, identify the repository URL and put it in the objective. If the user doesn't provide one directly:

1. **Check memory** — `memory_search` for the repo name, project name, or related keywords.
2. **Ask the user** — if memory doesn't have it, just ask.

## Reviewing a PR

Do not spawn a coding subagent to review a PR. Reviewing a PR reaches GitHub through the connector (`call_external_tool` against the `GITHUB_*` actions), which the coding subagent's sandbox tools do not include. Load the `code-review` skill and follow it directly: it fetches the diff through the connector, reviews change-by-change, and reports findings.

## Mixed Tasks

When a task involves both discovery and coding (e.g., "find tickets and implement them"), split it:

1. You do the discovery: find the repo URL, read tickets, gather requirements, search memory
2. Save context to workspace files (e.g., ticket details as markdown)
3. Delegate to the coding subagent with the repo URL and workspace file paths in the objective

"Discovery" means finding the repo URL, reading tickets/issues, and gathering user requirements. It does NOT mean reading source code, understanding the architecture, or exploring the codebase. That is the subagent's job.

## Post-Completion

After a coding subagent completes:

1. Read the subagent's full response carefully
2. Read the workspace files it produced
3. Summarize for the user:
   - **What was done**: What was implemented, fixed, or analyzed — mention specific changes, files modified, and approach taken
   - **Testing**: What tests were added or run, and their results
   - **Key decisions**: Any notable design decisions or trade-offs made
4. Share any generated files via `share_file`. A workspace with a git credential can `git push` a branch; opening a PR additionally needs the GitHub connector, which is what `gh` authenticates through. Without those, work comes back as files and patches in the workspace.

The summary should give the user a clear picture of the work without them needing to read the full diff. Be specific — mention function names, file paths, and concrete changes rather than vague descriptions.

## Examples

**Coding tasks:**

- "Fix the failing tests in this codebase"
- "Implement this Linear/Jira ticket"
- "Find the bug causing timeouts and fix it"
  → Find the repo URL and gather requirements yourself, then delegate with `spawn_subagent(profile="coding", ...)` and the repo URL in the objective. Include ticket details and any relevant context.

**Code review:**

- "Review this PR: github.com/acme/cobbledb/pull/42"
- "Code review PR #123 in acme/cobbledb"
  → Load the `code-review` skill and review the PR directly (through the GitHub connector) — do not spawn a coding subagent for review.

**Example spawn_subagent call (coding):**

```
spawn_subagent(
  profile="coding",
  payload={
    "objective": "Repository setup: clone https://github.com/acme/cobbledb into the workspace with git, then work inside it.\n\nRust codebase. Ticket LIN-1234: Add cursor-based pagination to the /query endpoint. Requirements: support `cursor` and `limit` query params, default limit 50, max 200. Write tests."
  }
)
```

For an unusually deep task, add `"extended_context": true` to the payload to run the child under the main agent's round ceiling instead of its default budget.
