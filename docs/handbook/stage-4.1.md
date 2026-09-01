# Built-In Workspace Apps and Packaged Agents  `stage-4.1`

This stage is shared setup for the apps and helper agents that come built into a workspace. It is not where chat messages are sent, code is edited, or research is performed. Instead, it is the catalog and installer layer that tells the host system what should exist when a workspace is created or extended.

The collaboration and knowledge app packages register user-facing tools such as Chat, Meetings, Radar, Wiki, Artifacts, and Documents-style shared spaces. Their manifest files act like ID cards, describing each app’s name, home screen, agent, permissions, and setup needs. The engineering and operations packages do the same for Code, Issues, and Metrics, so repository work, issue tracking, and operational reporting can be installed consistently.

The delegated worker packages add specialist helpers, such as browser, coding, research, and brief-writing agents, along with the tools and instructions they need. Finally, extension provisioning turns installed extension promises into real workspace agents, while keyed connector declarations describe API-key-based service access. Together, these pieces seed a workspace with ready-known apps, agents, tools, and safe connection rules.

## Sub-stages

- [Collaboration and Knowledge Workspace App Packages](stage-4.1.1.md) `stage-4.1.1` — 10 files
- [Engineering and Operations Workspace App Packages](stage-4.1.2.md) `stage-4.1.2` — 6 files
- [Delegated Worker and Workflow Extension Packages](stage-4.1.3.md) `stage-4.1.3` — 6 files
- [Extension Provisioning and Connector Declarations](stage-4.1.4.md) `stage-4.1.4` — 2 files
