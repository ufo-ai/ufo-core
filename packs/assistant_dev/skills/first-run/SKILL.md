---
name: first-run
description: "Load when a member starts a workspace, including a first-run handoff that states what they want an agent to do and which tools they use. Do not load for later work in an established workspace."
---

# First run

Turn the selected goal and tools into a useful plan. Do not assume the plan needs a new application.

## Read the workspace

Read the goal, free-text context, and tools in the opening message. Search memory for the company's
work, goals, and current state. Call `object_list(kind="agent")` so the plan uses applications the
workspace already has. The member's words override intake or memory. Check connector availability
only when it changes the plan.

Do not copy kickoff choices or connector state into memory. They already have records.

## Discover

Read only the reference for the selected goal:

- Faster product development: [references/faster-product-development.md](references/faster-product-development.md)
- More revenue: [references/more-revenue.md](references/more-revenue.md)
- Automate operations: [references/automate-operations.md](references/automate-operations.md)
- Find product-market fit: [references/find-product-market-fit.md](references/find-product-market-fit.md)

For a free-text goal that does not match these choices, do not read a reference. Apply the same
discovery rules to that goal.

Use one `ask_user` call with two to four unanswered questions from that reference. Ask about the
current state, the main constraint, and a measurable result. Do not ask what the company does when
memory or the opening message already says it. If the member already supplied enough detail, skip
the questions.

## Plan

After the answers, state the target and the current facts, then give three to five ordered steps.
Name the first measurable action, missing access, and where a person must decide. Prefer the main
assistant, an existing application, or a connected tool when it can do the work.

Create an application only when a repeatable job needs its own instructions, access, schedule, or
homepage. Present the plan first. If the member asks to build that application, load
`create-application` on their next turn and follow its interview. Do not create an application from
the first-run handoff.

End the plan with one single-select `ask_user` question: start the first step, or change the plan.
After the tool returns, write the full plan in the reply. Stop first-run behavior when the member
starts real work.
