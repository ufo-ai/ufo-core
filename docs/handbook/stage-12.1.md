# Provider-specific source connectors  `stage-12.1`

This stage is the system’s library of source connectors. It runs during the main data sync work, after the system knows which outside service it should read from. A connector is an adapter that signs in to a provider’s API, asks for allowed data, follows page-by-page results, and reshapes each item into the project’s standard record format.

The shared REST foundation provides the common plumbing for many web APIs: authentication, retries after temporary failures, pagination, and cursors that remember where a sync left off. On top of that base, provider-specific groups handle different worlds of work. Google Workspace connectors read mail, calendars, files, documents, spreadsheets, and meeting notes. Collaboration connectors bring in knowledge from tools like Slack, Teams, Notion, Outlook, and Confluence. Engineering connectors cover GitHub, Jira, Linear, PagerDuty, and Sentry. Other groups read work management, CRM, support, marketing, HR, recruiting, finance, billing, accounting, and commerce systems. Together, these parts turn many different SaaS products into one reliable stream of searchable records.

## Sub-stages

- [Shared REST source connector foundation](stage-12.1.1.md) `stage-12.1.1` — 1 files
- [Google Workspace source connectors](stage-12.1.2.md) `stage-12.1.2` — 6 files
- [Collaboration, messaging, and knowledge source connectors](stage-12.1.3.md) `stage-12.1.3` — 5 files
- [Engineering, issue tracking, and incident source connectors](stage-12.1.4.md) `stage-12.1.4` — 5 files
- [Work management, scheduling, and forms source connectors](stage-12.1.5.md) `stage-12.1.5` — 7 files
- [CRM, sales, and customer support source connectors](stage-12.1.6.md) `stage-12.1.6` — 6 files
- [Marketing, advertising, and social source connectors](stage-12.1.7.md) `stage-12.1.7` — 6 files
- [HR, recruiting, and workforce source connectors](stage-12.1.8.md) `stage-12.1.8` — 6 files
- [Finance, billing, accounting, and commerce source connectors](stage-12.1.9.md) `stage-12.1.9` — 7 files
