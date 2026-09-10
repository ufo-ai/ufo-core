export type TourPoint = { text: string; actor: string };

export const RADAR_TOUR: {
  title: string;
  lead: string;
  summary: string;
  points: TourPoint[];
  body: string;
} = {
  title: "What this workspace can do",
  lead: "Start here",
  summary: "What the workspace does, what to connect first, and where the work lands. Every scheduled run reports on this rail: the reply it closed with and the files it shared.",
  points: [
    {
      text: "The whole team shares the agents, in Slack, in the browser, and in the terminal. Conversations are shared or private.",
      actor: "Everyone",
    },
    {
      text: "Connect the CRM and the mailbox, then ask for lead research and outreach drafts.",
      actor: "Sales",
    },
    {
      text: "Connect GitHub, then ask for a pull request review or a scheduled code check.",
      actor: "Engineering",
    },
    {
      text: "Ask for the logo sheet the deploy ships, and read it in the Artifacts app with every later file.",
      actor: "Artifacts",
    },
  ],
  body: `## Conversations: shared or private

Every member reaches the same agents and the same conversations, minus the ones a scope keeps private. A conversation has a scope that controls who can read it and what the agent may read:

- **Shared.** A conversation shared with the workspace. Any teammate can open it, add to it, and read it.
- **Private.** A direct message. No other member can open it or see it listed.
- **Channels.** A Slack channel has its own conversation. Members outside the channel cannot read it.

Memory is scoped the same way. Memories created in a private conversation or from private data sources are not shared.

Three surfaces reach the workspace:

- **Slack.** Direct message the agent, or mention it in a channel and it replies in the thread.
- **The web portal.** Chat in the browser.
- **The terminal.** Install with \`curl -fsSL https://ufo.ai/ufo | sh\`, then run \`ufo\`.

## Set up sales work

1. Connect the CRM and the mailbox. Say "connect HubSpot" or "connect my email", then approve the connection the agent sends back.
2. Ask for lead research. State the market, the company size, and the role you sell to.
3. Ask for outreach drafts. Give one message you like, and the agent writes the rest in that voice.
4. Put the work on a schedule: "every Monday at 08:00, find new leads and draft the emails".

## Set up engineering work

1. Connect GitHub the same way. Name the repositories the team works in.
2. Ask for a pull request review. The agent reads the changes and writes what it found.
3. Schedule a code check: "every morning, read the open pull requests and report what is broken".

## The object system

The workspace keeps its parts as objects. Every persistent resource is an object with access controls, addressable from any conversation. The common kinds:

- **Agents** — the apps and assistants, each with its own prompt, model, and homepage.
- **Sites** — access controlled realtime websites the workspace hosts.
- **Skills** — a procedure an agent loads for one kind of job.
- **Scheduled tasks** — recurring jobs on a schedule.
- **Sources** — connections that feed memory and fire triggers.
- **Credentials** — keys a service needs. An admin fills one in a private prompt.

Objects support list, read, update, and delete operations from chat: "list the scheduled tasks", "show me the sites". Installed apps register additional object kinds.

## Connectors: memory and triggers

The sync runner polls each registered source, writes the content it finds to memory, and fires triggers when content changes.

\`\`\`mermaid
graph LR
    subgraph services[Connected services]
        gh[GitHub]
        drive[Shared drive]
        feed[Feeds]
    end
    sync[Sync runner] -->|polls| services
    sync -->|summarizes| mem
    sync -->|content change| trig[Trigger]
    mem -->|recall answers| conv[Conversation]
    trig -->|wakes| conv
\`\`\`

- **Memory.** Registered sources are read on a schedule and their content is written to memory, so recall can answer from it later. A source registered by one member is readable only by that member; a source registered for the workspace is readable by every member.
- **Triggers.** When a source changes, the trigger sends the change to its conversation. The conversation runs a standing instruction, such as reviewing the pull request, summarizing the new document, or posting the update.

## Connect other services

Connectors reach the services the team uses: mail, calendars, CRMs, project trackers, analytics, databases, and more. Name the service and the agent starts the connection.

## Choose the model

Each agent runs on the model you pick, or on the automatic choice. Say "run the code app on a stronger model" to change one app and leave the rest as they are.

## Artifacts

An artifact is a file the work produced: a report, a spreadsheet, an image, or a hosted site. The Artifacts app lists every one with the run that produced it.

The workspace already holds one file to ask for: "ufo-logo-ratio.pdf", the logo sheet, with the mark's proportions and the spacing around it. Say "share the logo sheet" and it lands in Artifacts as the first file of your own.
`,
};
