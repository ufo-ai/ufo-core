import { Figure, Prose } from "@/blocks/typography";

const THREAD =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='640' height='96'%3E" +
  "%3Cg fill='%233a3d3d'%3E%3Crect x='41' width='180' height='96' rx='6'/%3E%3Crect x='230' width='180' height='96' rx='6'/%3E%3Crect x='419' width='180' height='96' rx='6'/%3E%3C/g%3E" +
  "%3Cg fill='%23faf9f7' opacity='.18'%3E" +
  "%3Crect x='57' y='20' width='116' height='8' rx='4'/%3E%3Crect x='57' y='40' width='148' height='8' rx='4'/%3E%3Crect x='57' y='60' width='88' height='8' rx='4'/%3E" +
  "%3Crect x='246' y='20' width='132' height='8' rx='4'/%3E%3Crect x='246' y='40' width='104' height='8' rx='4'/%3E%3Crect x='246' y='60' width='148' height='8' rx='4'/%3E" +
  "%3Crect x='435' y='20' width='96' height='8' rx='4'/%3E%3Crect x='435' y='40' width='140' height='8' rx='4'/%3E%3Crect x='435' y='60' width='112' height='8' rx='4'/%3E" +
  "%3C/g%3E%3C/svg%3E";

export default function TypographyDocument() {
  return (
    <Prose>
      <h1>Turn a client issue into a tracked issue</h1>

      <h2>What this covers</h2>
      <p>
        A client reports a problem in a shared channel. The workspace agent reads the thread, opens a tracked issue and
        links it back to the message it came from. Nobody retypes the report.
      </p>
      <p>
        The issue carries the reporter, the account and the original text. Every later change to it is a turn in the
        conversation that produced it.
      </p>
      <Figure src={THREAD} alt="A channel thread beside the issue and the subtasks it produced" />

      <h2>Before you start</h2>
      <p>
        Connect the channel the client writes in and the tracker the issue lands in. Both connections are made in chat:
        name the account, approve the access the agent asks for, and the grant is stored against the workspace.
      </p>
      <p>
        An agent reaches only what the workspace granted it. A connector added by one member is available to everyone in
        that workspace.
      </p>

      <h2>How it works</h2>
      <p>
        The agent watches the connected channel. A message that reads as a defect starts a draft issue, and the draft is
        shown in the thread before anything is written to the tracker.
      </p>
      <p>
        Approve the draft and the issue is created with the thread attached. Reject it and the draft is dropped; the
        message stays where it was.
      </p>

      <h2>Setup</h2>
      <ol className="blk-muted">
        <li>Connect the channel the client writes in.</li>
        <li>Connect the tracker that receives the issue.</li>
        <li>Name the project new issues belong to.</li>
      </ol>
    </Prose>
  );
}
