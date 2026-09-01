# Delegation, Subagents, Objectives, and Multi-Step Workflows  `stage-11`

This stage is for work that cannot be finished with one simple tool call. It sits behind the main work loop and helps the agent break big jobs into smaller jobs, track them, and recover if work pauses or takes a long time.

One half is delegation. The system can start “subagents,” which are smaller worker agents with their own instructions and tools. A dispatcher checks that the worker is allowed, packages the task, starts the child conversation, waits for the answer when needed, and returns it to the main agent. A recovery piece looks for finished child work that was saved but not delivered, so results are not lost. Special workers handle browsing, research, deep research, wide parallel research, and website building.

The other half is objective and workflow tracking. It acts like a project notebook and checklist. The agent can define a goal, record steps and evidence, run promised checks, delegate parts, and only mark work complete after verification. Specialized workflows reuse this machinery for building apps, auditing them, and producing written briefs through outline, draft, and critique steps.

## Sub-stages

- [Subagent Dispatch and Recovery](stage-11.1.md) `stage-11.1` — 9 files
- [Objective and Workflow State Machines](stage-11.2.md) `stage-11.2` — 7 files

## 📊 State Registers Touched

- `reg-durable-database` — The main long-term database where shared business and runtime records are stored.
- `reg-agent-configuration` — Each agent’s saved settings, such as model choice, reasoning mode, tools, visibility, internet access, sandbox size, and setup needs.
- `reg-tool-catalog-allowlists` — The shared list of tools and actions an agent may see or run, including extension tools and sandbox bridge tools.
- `reg-skill-prompt-library` — The reusable instructions, skills, prompt rules, and agent setup guidance loaded into turns.
- `reg-conversation-turn-queue` — The durable state of conversations and turns, including admission, ordering, current runner, lifecycle status, and queued work.
- `reg-transcript-history` — The saved conversation transcript, summaries, compactions, and access records that preserve what happened in a chat.
- `reg-delegation-workflows` — The saved state for subagents, parent-child turns, objectives, workflow checkpoints, pending deliveries, and recovery.
- `reg-observability-trace` — The logs, metrics, traces, health signals, and trace links used to understand what the system is doing.
- `reg-active-turn-cancellation-handles` — The in-process registry of currently running turn/workflow tasks and cancellation handles used to stop active work before marking it cancelled durably.
- `reg-execution-step-log` — The structured persisted model, tool, and workflow execution records that power debugger timelines and post-run inspection beyond the user transcript.
