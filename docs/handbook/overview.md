# System Handbook

## 🗺️ System Overview

This system is an AI agent runtime: a place where assistants can talk with people, use tools, work with files and websites, connect to outside services, and hand off tasks to helper agents. A useful mental picture is a busy workshop. The assistant is at the main bench, but the workshop also has locked tool cabinets, safe rooms for risky work, filing systems, background clerks, and many doors for different users and apps.

Before it opens, the system is packed for deployment and the database is updated so old saved data still fits the current code. When the process starts, command-line tools, configuration, feature flags, storage, web servers, workers, sandboxes, and extensions are wired together. A chosen “pack” decides which product shape to run, and approved extensions register their tools, agents, prompts, jobs, and integrations. On a fresh install, the system creates the first workspace, admin, assistant, demo conversation, and any extension-provided setup.

During normal use, requests arrive from the web app, terminal, Slack, iMessage, public links, operator tools, or sandboxed sites. The front gate checks identity and routes each request to the right workspace and conversation. Conversation work is admitted into a durable queue, so it can survive crashes. For each turn, the system builds the assistant’s temporary work environment: instructions, relevant skills, model choice, tools, files, permissions, and possible helper agents.

The agent then runs its loop. It reads the conversation, calls an AI model, streams replies, uses approved tools, asks subagents for help, and saves progress. Risky actions run through a sandboxed workbench, and outside services go through guarded connector and credential systems. Meanwhile, background jobs sync connected sources, build search indexes and memory, run scheduled tasks, watch for changes, and recover abandoned work.

When a turn ends or is cancelled, the system releases sandboxes, browsers, streams, leases, and child work, then records the final state honestly. Under everything are shared contracts for database records, transcripts, models, billing, extension APIs, storage, configuration, logging, feature flags, and safety checks, keeping the workshop organized and recoverable.

---

## See also

- [State-flow registers](register.md) — global state that flows across stages
- [Stage Index](index.md) — every stage and what it does
