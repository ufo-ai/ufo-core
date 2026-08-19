# Extension-owned schema migrations  `stage-2.2`

This stage is part of setup and upgrades, not the normal work loop. It gathers the database migrations owned by optional extensions. A migration is a careful change to stored data, like adding or reshaping drawers in a filing cabinet. These extensions only need their storage when they are installed, so their tables live outside the core system.

The coding and sources migrations move code review work from older inbox tables into a newer trigger-based model. The memory migrations create and improve storage for saved facts, pages, source tracking, audiences, and fast lookup indexes. Search, evaluation, and research migrations add shelves for test email and calendar data, searchable text chunks, and records of sources seen during research. Automation migrations store long-running state for monitors, objectives, scheduled pauses, and daily briefs. Hosted site and web migrations preserve web conversations and track sites built from them. Skill and sample migrations add simple extension-owned examples: workspace notes and user-created agent skills. Together, these migrations let optional features keep durable state safely as the system grows.

## Sub-stages

- [Coding review and source trigger migrations](stage-2.2.1.md) `stage-2.2.1` — 6 files
- [Memory core storage and indexing migrations](stage-2.2.2.md) `stage-2.2.2` — 6 files
- [Memory provenance, audience, and source partition migrations](stage-2.2.3.md) `stage-2.2.3` — 6 files
- [Search, evaluation, and research data migrations](stage-2.2.4.md) `stage-2.2.4` — 4 files
- [Automation, objectives, monitors, and brief migrations](stage-2.2.5.md) `stage-2.2.5` — 5 files
- [Hosted site and web conversation migrations](stage-2.2.6.md) `stage-2.2.6` — 6 files
- [User-created skill and sample note migrations](stage-2.2.7.md) `stage-2.2.7` — 3 files
