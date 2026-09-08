---
name: coding
description: Load when asked to inspect or change source code, fix bugs, produce patches or diffs, work in or clone repositories, use pinned commits, or check GitHub access. On repository tasks, load coding first and call no other tool until it returns.
---
# Coding Subagent Routing

**Scope:** Spawn a coding subagent when the task requires navigating a repository or coding files. Do NOT spawn one for general questions that don't involve code or repos.

**Your turn on a repository task is exactly this procedure — nothing else:**

Call `spawn(target="coding", ...)` immediately after this skill loads. Do not call
`update_todo_list`, shell, file, web, or any setup tool first.

1. Choose the repository setup mode (below) and `spawn(target="coding", ...)` with the setup sentence as the objective's first line, followed by the task and any instructions the request carried for whoever does the work, verbatim.
2. Read the child's report; `read` its deliverable if you must inspect it.
3. Link the file the child wrote with Markdown under the shared delivery register, or use
   `share_file` when the member's ask carries a share trigger, then reply.

After a bare foreground `spawn` raises `TimeoutError`, do not infer the child stopped and do not
expect a later result to be delivered. Do not inspect, pause, or spawn a duplicate. Report that the
worker may still be running and that result delivery is not guaranteed. Do not tell the member to
retry or ask again later.

Every shell command in your turn is attributed to you, not to a child: a single `git`, `mkdir`, or `ls` in your own turn marks the task as done by you rather than delegated, however helpful it felt. Setup is not an exception — the clone or fetch is the first child's first act, and "the checkout already exists" is never something you made true yourself. If you are about to open a shell, you have left the procedure; spawn a child instead.

BAD — this marks the task as yours, whatever happens next: `mkdir -p /workspace/repo && git fetch --depth 1 origin <sha> && git checkout FETCH_HEAD` in your own turn, then spawning with "the checkout already exists." GOOD — spawn first: the objective's first line carries the setup sentence, and the child runs the fetch as its first act.

Do NOT:

- Browse the repo's directory structure or file contents
- Read, fetch, or open any source code files (e.g., a raw.githubusercontent.com or GitHub file URL)
- Try to "understand the architecture" or "get a complete picture" before delegating
- Use any tool to inspect code contents — not even a single file
- Browse the repo via `gh api` or URL fetching to read file trees, directory structures, or file contents
- Prepare `/workspace` — the child creates its own paths, runs its own setup, and verifies its own work (applying the patch, running tests) before it reports; put those requirements in the objective

The coding subagent works in your `/workspace` with `bash`, `git`, `read`, `write`, and `edit`, and can navigate codebases far more effectively than you can. Its checkout, edits, and notes land where you and every later child read them. Pass any non-code context (tickets, requirements, user instructions) in the objective — let the subagent explore the code.

## Repository Setup Contract

The main agent owns the clone/no-clone decision — the decision only. Every setup command, clone or pinned fetch alike, runs inside the first child; a `git` command in your own turn is this skill misapplied. Do not make the coding subagent infer whether cloning is needed from vague phrases like "in the repo" or "in this codebase."

One remote clone per turn. Children work in your `/workspace`, so the first remote clone is the only fetch: pick the path yourself and give it to the first spawn. You do not run the clone — the first child does, which keeps you out of the repository.

Sequence every spawn that uses one checkout. The cloning child finishes before another child touches that path; after it finishes, a later child may reuse the path. Never point two live children at one checkout: they collide over `.git/index.lock`, branch state, and working files. Give every overlapping child its own local checkout.

For fan-out, the cloning child only creates and verifies the canonical checkout. End its setup with `verify the checkout, report the checked-out branch as the base, then finish without task work.` After it finishes, start every worker in local checkout mode at a distinct path. Use the branch the setup child reports as `<base>`. For one task, the cloning child may work in the checkout itself.

Before calling `spawn(target="coding", ...)`, choose exactly one setup mode and state it at the start of the objective:

- **Clone a repo:** Use for the first spawn of the turn that needs the repository. Start the objective with: `Repository setup: clone https://github.com/org/repo into /workspace/org-repo with git, then work inside it.` For fan-out, use: `Repository setup: clone https://github.com/org/repo into /workspace/org-repo with git, verify the checkout, report the checked-out branch as the base, then finish without task work.` Name that path and reuse it below. A public repository clones with no connection; a private one needs the member's GitHub account connected (below).

  **A clone that fails to authenticate means GitHub is not connected. Start the `connect_account` handoff with `provider: github` — that is the next action, not a fallback route.** Reaching the files another way is the trap here, and every route is forbidden, not just the obvious one: no `gh api` file reads, and no zipball, tarball, or raw-content fetch through any tool. A snapshot fetched that way has no `.git`, cannot push, and hides from the member that nothing is connected.
- **Pinned commit:** Use when the task names an exact commit. Start the objective with: `Repository setup: fetch commit <sha> from https://github.com/org/repo into /workspace/org-repo with git at depth 1 and check out exactly that commit, then work inside it. The checkout is the only route to the files — no raw.githubusercontent.com, zipball, or tarball fetches.` As with a clone, you never run the fetch — the first child does.
- **Existing checkout:** Use for any later spawn after the child using that path has finished. Start the objective with: `Repository setup: use the existing checkout at /workspace/org-repo, from https://github.com/org/repo. Do not clone.` The URL is what the child falls back to if the path is not there.
- **Local checkout:** Use for every spawn that overlaps another child. Start the objective with: `Repository setup: copy the committed tree at /workspace/org-repo to /workspace/org-repo-<slug> with git, base the work on <base>, keep the source as workspace, use https://github.com/org/repo as origin, then work inside it.` This copies from disk without a fetch, separates the source's branches from GitHub's, and keeps pushes pointed at GitHub.
- **No repository:** Use only for coding-adjacent tasks that do not need repository files. Start the objective with: `Repository setup: no repository clone is needed.`

A coding subagent should not spend startup time deciding whether to clone.

When the requesting message's `<context>` carries a `source`, put it in the objective too — a subagent never sees the parent's context — so a PR or issue it opens can name where the request came from.

## Connecting GitHub

One connection covers GitHub whole: private `git clone` and `git push`, `gh`, and issue and pull-request reads and writes through the API all ride the member's connected GitHub account. When a clone fails to authenticate or a GitHub API call is refused, start the handoff with `connect_account` carrying `provider: github` — even when a connector listing shows no GitHub row, because the listing is not the verdict: the call either begins the handoff or names the deploy's actual gap. The member authorizes on GitHub and the connection completes itself; nothing is pasted back and no token is ever typed. Never accept a token pasted into the conversation.

Inside the sandbox `GH_TOKEN` holds a sentinel, never the token, and `gh`, `git clone`, and `git push` authenticate through it. A connection is the member's own, so a clone that works for one member and not another means the second member has not connected — say so rather than citing the first member's account.

## Finding the Repository

For GitHub-backed tasks, identify the repository URL and put it in the objective. If the user doesn't provide one directly:

1. **Check memory** — the memory kind's `memory_search` action for the repo name, project name, or related keywords.
2. **Ask the user** — if memory doesn't have it, just ask.

## Mixed Tasks

When a task involves both discovery and coding (e.g., "find tickets and implement them"), split it:

1. You do the discovery: find the repo URL, read tickets, gather requirements, search memory
2. Save context to workspace files (e.g., ticket details as markdown)
3. Delegate to the coding subagent with the repo URL and workspace file paths in the objective

"Discovery" means finding the repo URL, reading tickets/issues, and gathering user requirements. It does NOT mean reading source code, understanding the architecture, or exploring the codebase. That is the subagent's job.

## Returned files

A coding subagent has no `share_file`. Its files remain in the shared `/workspace` for the parent to
deliver under the shared delivery register. A push and a GitHub API write both use the member's
connected GitHub account.

## Examples

**Coding tasks:**

- "Fix the failing tests in this codebase"
- "Implement this Linear/Jira ticket"
- "Find the bug causing timeouts and fix it"
  → Find the repo URL and gather requirements yourself, then delegate with `spawn(target="coding", ...)` and the repo URL in the objective. Include ticket details and any relevant context.

**Example spawn call (coding):**

```
spawn(
  target="coding",
  payload={
    "objective": "Repository setup: clone https://github.com/acme/cobbledb into /workspace/acme-cobbledb with git, then work inside it.\n\nRust codebase. Ticket LIN-1234: Add cursor-based pagination to the /query endpoint. Requirements: support `cursor` and `limit` query params, default limit 50, max 200. Write tests."
  }
)
```

A second spawn after the first finishes opens with `Repository setup: use the existing checkout at /workspace/acme-cobbledb, from https://github.com/acme/cobbledb. Do not clone.`

**Example fan-out (two concurrent children, one repository):**

```
spawn(
  target="coding",
  payload={
    "objective": "Repository setup: clone https://github.com/acme/cobbledb into /workspace/acme-cobbledb with git, verify the checkout, report the checked-out branch as the base, then finish without task work."
  }
)
```

After it reports the base branch, start the workers concurrently, each at its own path:

```
spawn(
  target="coding",
  payload={
    "objective": "Repository setup: copy the committed tree at /workspace/acme-cobbledb to /workspace/acme-cobbledb-files with git, base the work on main, keep the source as workspace, use https://github.com/acme/cobbledb as origin, then work inside it.\n\nReport the number of files tracked in git."
  }
)
spawn(
  target="coding",
  payload={
    "objective": "Repository setup: copy the committed tree at /workspace/acme-cobbledb to /workspace/acme-cobbledb-commit with git, base the work on main, keep the source as workspace, use https://github.com/acme/cobbledb as origin, then work inside it.\n\nReport the current commit."
  }
)
```

Never start two children that each clone the repository, even to different paths: the one remote clone is the setup child's, and every worker copies from it.

For an unusually deep task, add `"extended_context": true` to the payload to run the child under the main agent's round ceiling instead of its default budget.
