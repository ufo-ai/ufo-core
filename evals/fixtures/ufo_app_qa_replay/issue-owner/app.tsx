import { useState } from "ufo/kit";
import { mountApp } from "ufo/kit";

interface IssueItem {
  id: number;
  title: string;
  ageHours: number;
  area: string;
  labels: string[];
  suggestedOwner: string;
  ownerFullName: string;
  currentLoad: number;
  timezone: string;
  reason: string;
  status: string;
}

interface MemberItem {
  login: string;
  name: string;
  areas: string[];
  openIssues: number;
  timezone: string;
}

const MEMBERS: MemberItem[] = [
  {
    login: "alex",
    name: "Alex",
    areas: ["platform", "queues"],
    openIssues: 2,
    timezone: "America/Los_Angeles",
  },
  {
    login: "priya",
    name: "Priya",
    areas: ["billing", "payments"],
    openIssues: 1,
    timezone: "America/New_York",
  },
  {
    login: "dana",
    name: "Dana",
    areas: ["frontend", "design systems"],
    openIssues: 6,
    timezone: "Europe/London",
  },
];

const UNASSIGNED_ISSUES: IssueItem[] = [
  {
    id: 521,
    title: "Webhook retries lose delivery order",
    ageHours: 19,
    area: "platform",
    labels: ["platform", "urgent"],
    suggestedOwner: "alex",
    ownerFullName: "Alex",
    currentLoad: 2,
    timezone: "America/Los_Angeles",
    reason: "Owns platform and queues areas; lowest platform workload.",
    status: "Triage",
  },
  {
    id: 522,
    title: "Billing export omits refund fees",
    ageHours: 7,
    area: "billing",
    labels: ["billing"],
    suggestedOwner: "priya",
    ownerFullName: "Priya",
    currentLoad: 1,
    timezone: "America/New_York",
    reason: "Primary area match for billing; currently light load.",
    status: "Triage",
  },
];

function App() {
  const [selectedIssueId, setSelectedIssueId] = useState<number>(521);
  const [reviewStatus, setReviewStatus] = useState<string | null>(null);

  const selectedIssue =
    UNASSIGNED_ISSUES.find((item) => item.id === selectedIssueId) ||
    UNASSIGNED_ISSUES[0];

  const handleReviewInChat = () => {
    setReviewStatus(
      `Prepared assignment for #${selectedIssue.id} dispatched to chat for review.`
    );
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        backgroundColor: "var(--color-field)",
        color: "var(--color-ink)",
        fontFamily: "var(--font-sans, system-ui, sans-serif)",
        padding: "1.5rem",
        boxSizing: "border-box",
      }}
    >
      <div
        style={{
          maxWidth: "1360px",
          margin: "0 auto",
          display: "flex",
          flexDirection: "column",
          gap: "1.5rem",
        }}
      >
        {/* Header / Orientation */}
        <header
          id="header-region"
          data-app-region="header"
          data-app-role="orientation"
          style={{
            backgroundColor: "var(--color-surface)",
            border: "1px solid var(--color-edge)",
            borderRadius: "0.5rem",
            padding: "1.25rem 1.5rem",
            display: "flex",
            flexWrap: "wrap",
            justifyContent: "space-between",
            alignItems: "center",
            gap: "1rem",
          }}
        >
          <div>
            <h1
              style={{
                margin: 0,
                fontSize: "1.5rem",
                fontWeight: 600,
                color: "var(--color-ink)",
              }}
            >
              Issue Ownership &amp; Triage
            </h1>
            <p
              style={{
                margin: "0.25rem 0 0 0",
                fontSize: "0.875rem",
                color: "var(--color-ink-soft)",
              }}
            >
              evalco/app • 2 unassigned issues require triage
            </p>
          </div>

          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.75rem",
              flexWrap: "wrap",
            }}
          >
            <div
              style={{
                padding: "0.375rem 0.75rem",
                backgroundColor: "var(--color-field)",
                borderRadius: "0.25rem",
                fontSize: "0.8125rem",
                border: "1px solid var(--color-edge)",
              }}
            >
              <span style={{ color: "var(--color-ink-soft)" }}>
                Open Issues:{" "}
              </span>
              <strong style={{ color: "var(--color-ink)" }}>8</strong>
            </div>
            <div
              style={{
                padding: "0.375rem 0.75rem",
                backgroundColor: "var(--color-field)",
                borderRadius: "0.25rem",
                fontSize: "0.8125rem",
                border: "1px solid var(--color-attention, #fca5a5)",
                color: "var(--color-attention-ink, #b91c1c)",
                fontWeight: 600,
              }}
            >
              Needs Triage: 2 issues
            </div>
          </div>
        </header>

        {/* Main 2-column Grid */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))",
            gap: "1.5rem",
            alignItems: "start",
          }}
        >
          {/* Left Column: Unassigned Issues */}
          <section
            id="unassigned-issues-region"
            data-app-region="unassigned-issues"
            style={{
              backgroundColor: "var(--color-surface)",
              border: "1px solid var(--color-edge)",
              borderRadius: "0.5rem",
              padding: "1.5rem",
              display: "flex",
              flexDirection: "column",
              gap: "1rem",
            }}
          >
            <div>
              <h2
                style={{
                  margin: 0,
                  fontSize: "1.125rem",
                  fontWeight: 600,
                  color: "var(--color-ink)",
                }}
              >
                Unassigned Newly Opened Issues
              </h2>
              <p
                style={{
                  margin: "0.25rem 0 0 0",
                  fontSize: "0.8125rem",
                  color: "var(--color-ink-soft)",
                }}
              >
                Select an issue to inspect suggested owner matching, load stats,
                and review chat handoff.
              </p>
            </div>

            <div
              role="list"
              aria-label="Unassigned issues list"
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "0.875rem",
              }}
            >
              {UNASSIGNED_ISSUES.map((issue) => {
                const isSelected = issue.id === selectedIssueId;
                return (
                  <button
                    key={issue.id}
                    type="button"
                    role="listitem"
                    aria-pressed={isSelected}
                    aria-label={`Issue #${issue.id}: ${issue.title}`}
                    onClick={() => {
                      setSelectedIssueId(issue.id);
                      setReviewStatus(null);
                    }}
                    style={{
                      textAlign: "left",
                      backgroundColor: isSelected
                        ? "var(--color-field)"
                        : "var(--color-surface)",
                      border: isSelected
                        ? "2px solid var(--accent-primary, #2563eb)"
                        : "1px solid var(--color-edge)",
                      borderRadius: "0.375rem",
                      padding: "1rem",
                      cursor: "pointer",
                      display: "flex",
                      flexDirection: "column",
                      gap: "0.5rem",
                      transition: "border-color 0.15s ease",
                      color: "inherit",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "flex-start",
                        gap: "0.5rem",
                      }}
                    >
                      <span
                        style={{
                          fontWeight: 600,
                          fontSize: "0.9375rem",
                          color: "var(--color-ink)",
                        }}
                      >
                        #{issue.id} {issue.title}
                      </span>
                    </div>

                    <div
                      style={{
                        display: "flex",
                        gap: "0.375rem",
                        flexWrap: "wrap",
                      }}
                    >
                      {issue.labels.map((lbl) => (
                        <span
                          key={lbl}
                          style={{
                            fontSize: "0.6875rem",
                            padding: "0.125rem 0.375rem",
                            borderRadius: "0.25rem",
                            fontWeight: 500,
                            backgroundColor:
                              lbl === "urgent"
                                ? "var(--color-attention, #fee2e2)"
                                : "var(--color-field)",
                            color:
                              lbl === "urgent"
                                ? "var(--color-attention-ink, #b91c1c)"
                                : "var(--color-ink)",
                            border: "1px solid var(--color-edge)",
                          }}
                        >
                          {lbl}
                        </span>
                      ))}
                    </div>

                    <div
                      style={{
                        fontSize: "0.8125rem",
                        color: "var(--color-ink)",
                        display: "flex",
                        flexDirection: "column",
                        gap: "0.25rem",
                        marginTop: "0.25rem",
                      }}
                    >
                      <div>
                        <span style={{ color: "var(--color-ink-soft)" }}>
                          Age:{" "}
                        </span>
                        <strong>{issue.ageHours} hours</strong> •{" "}
                        <span style={{ color: "var(--color-ink-soft)" }}>
                          Area:{" "}
                        </span>
                        <strong>{issue.area}</strong> •{" "}
                        <span style={{ color: "var(--color-ink-soft)" }}>
                          Status:{" "}
                        </span>
                        <strong>{issue.status}</strong>
                      </div>
                      <div>
                        <span style={{ color: "var(--color-ink-soft)" }}>
                          Suggested Owner:{" "}
                        </span>
                        <strong
                          style={{
                            color: "var(--accent-primary, #2563eb)",
                          }}
                        >
                          {issue.suggestedOwner}
                        </strong>{" "}
                        ({issue.ownerFullName}, {issue.currentLoad} open{" "}
                        {issue.currentLoad === 1 ? "issue" : "issues"})
                      </div>
                      <div
                        style={{
                          fontSize: "0.75rem",
                          color: "var(--color-ink-soft)",
                          marginTop: "0.125rem",
                        }}
                      >
                        Reason: {issue.reason}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          </section>

          {/* Right Column: Prepared Assignment Review & Team Load */}
          <section
            id="action-review-region"
            data-app-region="action-review"
            data-app-role="primary-action"
            style={{
              backgroundColor: "var(--color-surface)",
              border: "1px solid var(--color-edge)",
              borderRadius: "0.5rem",
              padding: "1.5rem",
              display: "flex",
              flexDirection: "column",
              gap: "1.25rem",
            }}
          >
            <div>
              <h2
                style={{
                  margin: 0,
                  fontSize: "1.125rem",
                  fontWeight: 600,
                  color: "var(--color-ink)",
                }}
              >
                Prepared Assignment Review
              </h2>
              <p
                style={{
                  margin: "0.25rem 0 0 0",
                  fontSize: "0.8125rem",
                  color: "var(--color-ink-soft)",
                }}
              >
                Assignments are made in chat
              </p>
            </div>

            {/* Proposal Details Card */}
            <div
              style={{
                backgroundColor: "var(--color-field)",
                border: "1px solid var(--color-edge)",
                borderRadius: "0.375rem",
                padding: "1rem",
                display: "flex",
                flexDirection: "column",
                gap: "0.75rem",
              }}
            >
              <div
                style={{
                  fontWeight: 600,
                  fontSize: "0.875rem",
                  borderBottom: "1px solid var(--color-edge)",
                  paddingBottom: "0.5rem",
                  color: "var(--color-ink)",
                }}
              >
                Active Proposal: Assign #{selectedIssue.id} to{" "}
                {selectedIssue.ownerFullName}
              </div>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "110px 1fr",
                  gap: "0.5rem 0.75rem",
                  fontSize: "0.8125rem",
                }}
              >
                <div style={{ color: "var(--color-ink-soft)" }}>
                  Target Issue
                </div>
                <div style={{ fontWeight: 500, color: "var(--color-ink)" }}>
                  #{selectedIssue.id} • {selectedIssue.title}
                </div>

                <div style={{ color: "var(--color-ink-soft)" }}>
                  Assigned Area
                </div>
                <div style={{ fontWeight: 500, color: "var(--color-ink)" }}>
                  {selectedIssue.area}
                </div>

                <div style={{ color: "var(--color-ink-soft)" }}>Candidate</div>
                <div style={{ fontWeight: 500, color: "var(--color-ink)" }}>
                  {selectedIssue.ownerFullName} (@
                  {selectedIssue.suggestedOwner} • {selectedIssue.timezone})
                </div>

                <div style={{ color: "var(--color-ink-soft)" }}>
                  Current Load
                </div>
                <div style={{ fontWeight: 500, color: "var(--color-ink)" }}>
                  {selectedIssue.currentLoad} open{" "}
                  {selectedIssue.currentLoad === 1 ? "issue" : "issues"}
                </div>

                <div style={{ color: "var(--color-ink-soft)" }}>Rationale</div>
                <div style={{ color: "var(--color-ink)" }}>
                  {selectedIssue.reason}
                </div>

                <div style={{ color: "var(--color-ink-soft)" }}>
                  Safety Rule
                </div>
                <div
                  style={{
                    color: "var(--color-ink-soft)",
                    fontSize: "0.75rem",
                  }}
                >
                  Direct assignments disabled. Assignments are made in chat.
                </div>
              </div>
            </div>

            {/* Action Trigger */}
            <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
              <button
                type="button"
                aria-label="Review in chat"
                onClick={handleReviewInChat}
                style={{
                  backgroundColor: "var(--accent-primary, #2563eb)",
                  color: "var(--color-fill-ink, #ffffff)",
                  border: "none",
                  borderRadius: "0.25rem",
                  padding: "0.75rem 1rem",
                  fontSize: "0.875rem",
                  fontWeight: 600,
                  cursor: "pointer",
                  width: "100%",
                  textAlign: "center",
                }}
              >
                Review in chat
              </button>

              {reviewStatus && (
                <div
                  role="status"
                  aria-live="polite"
                  style={{
                    padding: "0.625rem 0.75rem",
                    backgroundColor: "var(--color-field)",
                    border: "1px solid var(--color-edge)",
                    borderRadius: "0.25rem",
                    fontSize: "0.8125rem",
                    color: "var(--color-ink)",
                  }}
                >
                  {reviewStatus}
                </div>
              )}
            </div>

            {/* Team Load Reference Table */}
            <div
              style={{
                marginTop: "0.5rem",
                display: "flex",
                flexDirection: "column",
                gap: "0.5rem",
              }}
            >
              <h3
                style={{
                  margin: 0,
                  fontSize: "0.9375rem",
                  fontWeight: 600,
                  color: "var(--color-ink)",
                }}
              >
                Repository Team Load &amp; Ownership Areas
              </h3>

              <div
                style={{
                  border: "1px solid var(--color-edge)",
                  borderRadius: "0.375rem",
                  overflow: "hidden",
                }}
              >
                <table
                  style={{
                    width: "100%",
                    borderCollapse: "collapse",
                    fontSize: "0.8125rem",
                  }}
                >
                  <thead>
                    <tr
                      style={{
                        backgroundColor: "var(--color-field)",
                        borderBottom: "1px solid var(--color-edge)",
                        textAlign: "left",
                      }}
                    >
                      <th
                        style={{
                          padding: "0.5rem 0.75rem",
                          fontWeight: 600,
                          color: "var(--color-ink)",
                        }}
                      >
                        Member
                      </th>
                      <th
                        style={{
                          padding: "0.5rem 0.75rem",
                          fontWeight: 600,
                          color: "var(--color-ink)",
                        }}
                      >
                        Areas
                      </th>
                      <th
                        style={{
                          padding: "0.5rem 0.75rem",
                          fontWeight: 600,
                          color: "var(--color-ink)",
                          textAlign: "right",
                        }}
                      >
                        Load
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {MEMBERS.map((member, index) => (
                      <tr
                        key={member.login}
                        style={{
                          borderBottom:
                            index < MEMBERS.length - 1
                              ? "1px solid var(--color-edge)"
                              : "none",
                        }}
                      >
                        <td
                          style={{
                            padding: "0.5rem 0.75rem",
                            fontWeight: 500,
                            color: "var(--color-ink)",
                          }}
                        >
                          {member.name} (@{member.login})
                        </td>
                        <td
                          style={{
                            padding: "0.5rem 0.75rem",
                            color: "var(--color-ink-soft)",
                          }}
                        >
                          {member.areas.join(", ")}
                        </td>
                        <td
                          style={{
                            padding: "0.5rem 0.75rem",
                            textAlign: "right",
                            fontWeight: 600,
                            color: "var(--color-ink)",
                          }}
                        >
                          {member.openIssues}{" "}
                          {member.openIssues === 1 ? "issue" : "issues"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

mountApp(document.getElementById("root")!, () => <App />);
