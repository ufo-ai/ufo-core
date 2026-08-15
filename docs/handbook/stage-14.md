# Specialized creation workflows for code, documents, sites, and briefs  `stage-14`

This stage is a collection of specialist toolboxes the system calls when a task needs more than ordinary chat. It is mostly behind-the-scenes support for making and editing real work products: code, websites, briefs, and Office or PDF documents.

For code work, the GitHub extension connects a workspace to a GitHub App and mints short-lived access tokens, like temporary keys for repositories. The website extension builds site files, runs them safely in a sandbox, and exposes a controlled preview link. The scratchpad and skill-authoring support gives agents reusable notebooks and a way to save their own small tools.

For writing, the brief pipeline turns a request into an outline, draft, and critique. The document review extension records issues and writes them back into PDFs, PowerPoint slides, or Excel files as visible comments. Separate DOCX, PPTX, XLSX, and PDF utilities open these file packages, repair or recalculate them, add comments or form data, render previews, and pack them back up. Together, these parts let agents produce polished artifacts, not just text replies.

## Sub-stages

- [Coding extension and GitHub App repository access](stage-14.1.md) `stage-14.1` — 3 files
- [Website building and hosting extension](stage-14.2.md) `stage-14.2` — 2 files
- [Agent scratchpad and skill-authoring extension support](stage-14.3.md) `stage-14.3` — 4 files
- [Brief-writing pipeline extension](stage-14.4.md) `stage-14.4` — 2 files
- [Document extension and review annotation workflow](stage-14.5.md) `stage-14.5` — 8 files
- [Word DOCX package and change/comment tools](stage-14.6.md) `stage-14.6` — 4 files
- [PowerPoint PPTX package, repair, and slide tools](stage-14.7.md) `stage-14.7` — 5 files
- [Excel XLSX recalculation tools](stage-14.8.md) `stage-14.8` — 3 files
- [PDF form, layout, and rendering utilities](stage-14.9.md) `stage-14.9` — 3 files

## 📊 State Registers Touched

- `reg-tool-catalog` — The runtime menu of tools the agent may call, including built-ins and extension-provided tools.
- `reg-credential-vault` — Encrypted workspace secrets and short-lived brokered credentials used without exposing raw secrets to tools.
- `reg-connection-grants` — Connected third-party accounts and permissions saying which agents may use which external accounts.
- `reg-sandbox-workspace` — The per-conversation isolated workbench, including its handle, files, execution backend, and recorded file changes.
- `reg-artifact-store` — Generated files, previews, blobs, and signed shared-artifact links that outlive a single message.
- `reg-hosted-site-state` — Hosted sandbox sites, their public addresses, generations, ports, visibility, and unhosting status.
- `reg-user-skill-library` — Persisted user- or agent-authored skills and reusable skill metadata loaded into the agent’s available capabilities.
- `reg-coding-review-state` — Coding review inboxes, review runs, source bindings, and conversation links used by the code-review workflow.
- `reg-scratchpad-notebooks` — Persisted scratchpad or notebook content that agents reuse across turns separately from saved skills and ordinary conversation files.
