# Provider-specific source connectors  `stage-13.1`

This stage is the system’s big set of source adapters. It sits behind the scenes in the data intake and sync process, not in the user-facing main work. Its job is to talk to many outside services through their APIs, which are web doorways for software to request data, and turn each service’s different shape into the system’s common shape: streams of records, pages of results, saved progress markers, readable text, updates, and deletes.

The framework and registry provide the shared plug shape and the address book for all connectors. The productivity connectors bring in Google and Microsoft mail, calendars, files, chats, and meetings. Project, developer, CRM, marketing, finance, HR, knowledge, scheduling, and community connectors each cover their own families of tools, such as Jira, GitHub, Salesforce, Stripe, BambooHR, Notion, Slack, and YC content. Together they work like many translators feeding one conveyor belt, so the rest of the system can sync, store, search, and recall data without needing to understand every provider’s quirks.

## Sub-stages

- [Source connector framework and registry](stage-13.1.1.md) `stage-13.1.1` — 3 files
- [Google and Microsoft productivity connectors](stage-13.1.2.md) `stage-13.1.2` — 8 files
- [Project and work-management connectors](stage-13.1.3.md) `stage-13.1.3` — 6 files
- [Developer operations and incident connectors](stage-13.1.4.md) `stage-13.1.4` — 3 files
- [CRM, sales, and customer-support connectors](stage-13.1.5.md) `stage-13.1.5` — 6 files
- [Marketing, advertising, social, and forms connectors](stage-13.1.6.md) `stage-13.1.6` — 7 files
- [Finance, billing, accounting, and commerce connectors](stage-13.1.7.md) `stage-13.1.7` — 7 files
- [HR, payroll, and recruiting connectors](stage-13.1.8.md) `stage-13.1.8` — 6 files
- [Knowledge, collaboration, scheduling, and community content connectors](stage-13.1.9.md) `stage-13.1.9` — 6 files
