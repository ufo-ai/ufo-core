import { useState, useMemo, useCallback } from "ufo/kit";
import { mountApp } from "ufo/kit";

interface MeetingData {
  id: string;
  title: string;
  timeRange: string;
  calendarStatus: string;
  attendees: string[];
  sourceRecency: {
    calendar: string;
    email: string;
    drive: string;
    github: string;
  };
  priorContext: {
    points: string[];
    sourceNote: string;
  };
  liveWork: {
    items: {
      type: "issue" | "pr" | "metric";
      id: string;
      title: string;
      meta: string;
      status: string;
      statusType: "success" | "warning" | "info" | "neutral";
    }[];
    summary: string;
  };
  missingOrUncertain: {
    items: string[];
    alertLevel: "warning" | "info";
  };
  pointsToRaise: {
    id: number;
    title: string;
    detail: string;
    rationale: string;
    source: string;
  }[];
}

const MEETINGS: MeetingData[] = [
  {
    id: "northstar",
    title: "Northstar renewal",
    timeRange: "16:00 – 16:30 UTC",
    calendarStatus: "Confirmed",
    attendees: ["maya@northstar.example", "jordan@northstar.example"],
    sourceRecency: {
      calendar: "Today (24 Aug, 16:00 UTC)",
      email: "21 Aug (3 days ago)",
      drive: "12 Aug (12 days ago)",
      github: "Today (18:00 UTC sync)"
    },
    priorContext: {
      points: [
        "Northstar offers a $48,000 renewal if single sign-on (SSO) is committed by 18 September.",
        "Jordan leads Northstar's security review and requested our final implementation schedule on today's call.",
        "Internal target date for the customer launch remains 14 September across core services.",
        "Billing cutover is scheduled for 2 September, with public post scheduled for Tuesday."
      ],
      sourceNote: "Drive notes from 12 August call and email from Maya on 21 August"
    },
    liveWork: {
      items: [
        {
          type: "issue",
          id: "#2077",
          title: "Northstar SSO security review",
          meta: "Assigned to Priya • Security scope review",
          status: "Open",
          statusType: "warning"
        },
        {
          type: "issue",
          id: "#412",
          title: "Confirm SSO launch scope",
          meta: "Assigned to Jordan • Specification sign-off",
          status: "Open",
          statusType: "warning"
        },
        {
          type: "pr",
          id: "#741",
          title: "Repair calendar source cursor",
          meta: "Alex • Approved • Conflict resolving via Gemini 3.7 Flash",
          status: "In Progress",
          statusType: "info"
        },
        {
          type: "pr",
          id: "#701",
          title: "Bound webhook delivery retries",
          meta: "Alex • Blocked on integration check failure",
          status: "Blocked",
          statusType: "warning"
        }
      ],
      summary: "32 PRs merged over 4 weeks with 29h average merge time. 78% linked to issues."
    },
    missingOrUncertain: {
      alertLevel: "warning",
      items: [
        "Final SSO technical specification scope is pending confirmation before Jordan can finish the security checklist.",
        "Drive meeting notes for Northstar are 12 days old (12 August); terms rely on Maya's 21 August email.",
        "Need confirmation whether our 14 September launch allows sufficient buffer for Northstar's 18 September deadline."
      ]
    },
    pointsToRaise: [
      {
        id: 1,
        title: "Commit to SSO delivery before 18 September",
        detail: "Confirm that our 14 September platform release satisfies Northstar's 18 September requirement to secure the $48,000 renewal.",
        rationale: "Removes contractual ambiguity and locks in annual recurring revenue.",
        source: "Email from Maya (21 Aug) & Drive call notes (12 Aug)"
      },
      {
        id: 2,
        title: "Hand off finalized SSO scope for Jordan's checklist",
        detail: "Provide Priya and Jordan the exact SSO scope definition so Jordan can unblock and finish the security review checklist (Issues #412 and #2077).",
        rationale: "Unblocks Northstar security clearance prior to implementation rollout.",
        source: "Launch review transcript (24 Aug) & GitHub Issue #412"
      },
      {
        id: 3,
        title: "Coordinate billing cutover with renewal timeline",
        detail: "Align the 2 September billing system transition with Northstar's renewal paperwork to ensure smooth invoicing continuity.",
        rationale: "Prevents invoicing errors during customer renewal processing.",
        source: "Launch review transcript (Dana)"
      }
    ]
  },
  {
    id: "product-design",
    title: "Product design sync",
    timeRange: "18:00 – 18:50 UTC",
    calendarStatus: "Confirmed",
    attendees: ["dana@evalco.test", "priya@evalco.test"],
    sourceRecency: {
      calendar: "Today (24 Aug, 18:00 UTC)",
      email: "21 Aug (3 days ago)",
      drive: "Today (24 Aug, 17:05 UTC)",
      github: "Today (18:00 UTC sync)"
    },
    priorContext: {
      points: [
        "Moved the first-run launch target date to 14 September.",
        "Closed #2042 after moving the new application button to the page footer per design review.",
        "Opened #2040 to decide the personal-domain login policy and user onboarding path.",
        "Priya committed to updating the support runbook by Friday, 28 August."
      ],
      sourceNote: "Drive design notes (21 Aug), email from Dana (21 Aug), and launch transcript (24 Aug)"
    },
    liveWork: {
      items: [
        {
          type: "issue",
          id: "#2040",
          title: "Decide personal-domain login policy",
          meta: "Assigned to member • Policy and flow decision needed",
          status: "Open",
          statusType: "warning"
        },
        {
          type: "issue",
          id: "#2042",
          title: "Move new application button to page foot",
          meta: "Dana • Implemented and closed in previous sprint",
          status: "Closed",
          statusType: "success"
        },
        {
          type: "issue",
          id: "#523",
          title: "Mobile settings spacing",
          meta: "Dana • Frontend refinement (3 hours old)",
          status: "Open",
          statusType: "info"
        },
        {
          type: "pr",
          id: "#703",
          title: "Tighten mobile settings grid",
          meta: "Dana • Approved, needs issue linkage to #523",
          status: "Ready",
          statusType: "info"
        }
      ],
      summary: "Active design and frontend PRs under review. Dana managing 6 open issues across design systems."
    },
    missingOrUncertain: {
      alertLevel: "info",
      items: [
        "Personal-domain login policy (#2040) remains unassigned to a technical spec pending today's decision.",
        "Account picker replacement (#603) is marked 'needs-product' with no approved UX spec yet.",
        "PR #743 (account health copy) currently has failing tests and 1 unresolved discussion thread."
      ]
    },
    pointsToRaise: [
      {
        id: 1,
        title: "Finalize personal-domain login policy (#2040)",
        detail: "Decide whether personal domains can create independent workspaces or require corporate domain verification.",
        rationale: "Unblocks user onboarding logic for the 14 September release.",
        source: "Email from Dana (21 Aug) & GitHub Issue #2040"
      },
      {
        id: 2,
        title: "Link mobile settings Issue #523 to PR #703",
        detail: "Confirm Dana's mobile settings grid improvements (PR #703) and link it to issue #523 to maintain clean tracking.",
        rationale: "Brings repository link compliance closer to the 85% team benchmark.",
        source: "GitHub PR #703 & Issue #523"
      },
      {
        id: 3,
        title: "Confirm support runbook handover for 28 August",
        detail: "Verify Priya's milestone to deliver the updated customer support runbook by 28 August ahead of the 2 September billing cutover.",
        rationale: "Ensures operational readiness before customer-facing billing changes.",
        source: "Launch review transcript (24 Aug)"
      }
    ]
  },
  {
    id: "investor-update",
    title: "Investor update review",
    timeRange: "21:00 – 21:30 UTC",
    calendarStatus: "Confirmed",
    attendees: ["investor@transpose.example"],
    sourceRecency: {
      calendar: "Today (24 Aug, 21:00 UTC)",
      email: "20 Aug (4 days ago)",
      drive: "No documents on record",
      github: "Today (18:00 UTC sync)"
    },
    priorContext: {
      points: [
        "Transpose investor requested focus on the 100-person waitlist, weekly burn, and activation rate shifts.",
        "Engineering delivery velocity improved: open-to-merge cycle dropped from 34 hours down to 24 hours.",
        "32 pull requests merged across the last 4-week reporting window with a 78% issue linkage rate.",
        "Billing error improvements (PR #874) merged to clarify Stripe invoice failure codes."
      ],
      sourceNote: "Email from Transpose (20 Aug) and GitHub delivery metrics (25 Jul - 23 Aug)"
    },
    liveWork: {
      items: [
        {
          type: "metric",
          id: "Merge Speed",
          title: "Open-to-merge down to 24h",
          meta: "Improved from 34h on 27 Jul to 24h on 17 Aug",
          status: "30% Faster",
          statusType: "success"
        },
        {
          type: "issue",
          id: "#601",
          title: "Export audit log by workspace",
          meta: "Alex • In Progress • PR #880 in draft passing tests",
          status: "In Progress",
          statusType: "info"
        },
        {
          type: "issue",
          id: "#602",
          title: "Show failed invoice reason",
          meta: "Priya • PR #702 has 2 review threads remaining",
          status: "In Review",
          statusType: "warning"
        },
        {
          type: "issue",
          id: "#410",
          title: "Publish billing cutover date",
          meta: "Dana • Open • Cutover set for 2 September",
          status: "Open",
          statusType: "info"
        }
      ],
      summary: "Engineering throughput is up 50% month-over-month (6 merges/wk to 9 merges/wk)."
    },
    missingOrUncertain: {
      alertLevel: "warning",
      items: [
        "No previous meeting notes found in Google Drive for investor reviews; agenda is inferred from 20 August email.",
        "Exact weekly burn figures are maintained in external accounting ledgers, not in connected GitHub/Drive sources.",
        "Cohort retention breakdown behind the 100-person waitlist needs live product analytics confirmation."
      ]
    },
    pointsToRaise: [
      {
        id: 1,
        title: "Report on the 100-person waitlist & activation changes",
        detail: "Walk through early feedback from the 100-person waitlist, explaining how revised onboarding and button placement improved activation.",
        rationale: "Directly addresses the investor's primary question from the 20 August email.",
        source: "Email from Transpose (20 Aug) & Issue #2042"
      },
      {
        id: 2,
        title: "Highlight 30% faster engineering cycle time",
        detail: "Present delivery metrics showing open-to-merge time reduction from 34 hours to 24 hours across 32 merged PRs.",
        rationale: "Demonstrates sustained engineering execution and team efficiency gains.",
        source: "GitHub delivery metrics report"
      },
      {
        id: 3,
        title: "Outline 14 September launch & 2 September billing cutover",
        detail: "Provide transparency on upcoming platform milestones: billing cutover on 2 September followed by public launch on 14 September.",
        rationale: "Establishes clear roadmap clarity and upcoming revenue inflection points.",
        source: "Launch review transcript (24 Aug)"
      }
    ]
  }
];

function App() {
  const [selectedMeetingIndex, setSelectedMeetingIndex] = useState(0);
  const [checkedPoints, setCheckedPoints] = useState<Record<string, boolean>>({});
  const [copyFeedback, setCopyFeedback] = useState<string | null>(null);
  const [actionCount, setActionCount] = useState<number>(0);
  const [lastActionMessage, setLastActionMessage] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [showOnlyActionable, setShowOnlyActionable] = useState(false);

  const currentMeeting = MEETINGS[selectedMeetingIndex];

  const handleTogglePoint = useCallback((meetingId: string, pointId: number) => {
    const key = `${meetingId}-${pointId}`;
    setCheckedPoints((prev) => ({
      ...prev,
      [key]: !prev[key]
    }));
  }, []);

  const handleCopyBrief = useCallback(async () => {
    const textToCopy = `PRE-MEETING BRIEF: ${currentMeeting.title}
Time: ${currentMeeting.timeRange}
Attendees: ${currentMeeting.attendees.join(", ")}
Calendar Status: ${currentMeeting.calendarStatus}

SOURCE RECENCY & AGE:
- Calendar: ${currentMeeting.sourceRecency.calendar}
- Email: ${currentMeeting.sourceRecency.email}
- Drive: ${currentMeeting.sourceRecency.drive}
- GitHub: ${currentMeeting.sourceRecency.github}

PRIOR CONTEXT:
${currentMeeting.priorContext.points.map((p) => `• ${p}`).join("\n")}

LIVE WORK:
${currentMeeting.liveWork.items.map((i) => `• ${i.id}: ${i.title} (${i.status}) - ${i.meta}`).join("\n")}

DATA GAPS & UNCERTAINTIES:
${currentMeeting.missingOrUncertain.items.map((m) => `• ${m}`).join("\n")}

POINTS TO RAISE:
${currentMeeting.pointsToRaise.map((p, idx) => `${idx + 1}. ${p.title}: ${p.detail}`).join("\n")}
`;

    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(textToCopy);
        setCopyFeedback("Brief copied to clipboard!");
      } else {
        setCopyFeedback("Clipboard unavailable. Summary prepared in view.");
      }
    } catch {
      setCopyFeedback("Could not access clipboard. Please copy manually.");
    }

    setTimeout(() => {
      setCopyFeedback(null);
    }, 4000);
  }, [currentMeeting]);

  const handleExecutePreparedAction = useCallback((title: string, msg: string) => {
    setActionCount((c) => c + 1);
    setLastActionMessage(`${title}: ${msg}`);
    setTimeout(() => {
      setLastActionMessage(null);
    }, 4000);
  }, []);

  const filteredPoints = useMemo(() => {
    return currentMeeting.pointsToRaise.filter((p) => {
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        const match =
          p.title.toLowerCase().includes(q) ||
          p.detail.toLowerCase().includes(q) ||
          p.rationale.toLowerCase().includes(q) ||
          p.source.toLowerCase().includes(q);
        if (!match) return false;
      }
      if (showOnlyActionable) {
        const isChecked = checkedPoints[`${currentMeeting.id}-${p.id}`];
        if (isChecked) return false;
      }
      return true;
    });
  }, [currentMeeting, searchQuery, showOnlyActionable, checkedPoints]);

  return (
    <div
      style={{
        minHeight: "100vh",
        backgroundColor: "var(--color-surface, #fcfcfc)",
        color: "var(--color-ink, #111111)",
        fontFamily: "var(--font-sans, Inter, system-ui, sans-serif)",
        padding: "1.5rem",
        boxSizing: "border-box"
      }}
    >
      <header
        id="region-header"
        data-app-region="header"
        data-app-role="orientation"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          flexWrap: "wrap",
          gap: "1rem",
          marginBottom: "1.5rem",
          borderBottom: "1px solid var(--color-edge, #e5e5e5)",
          paddingBottom: "1.25rem"
        }}
      >
        <div>
          <h1
            style={{
              margin: 0,
              fontSize: "1.625rem",
              fontWeight: 600,
              letterSpacing: "-0.02em",
              color: "var(--color-ink, #111111)"
            }}
          >
            Pre-Meeting Briefs
          </h1>
          <p
            style={{
              margin: "0.375rem 0 0 0",
              fontSize: "0.875rem",
              color: "var(--color-ink-soft, #676767)",
              lineHeight: 1.4
            }}
          >
            Interactive agenda synthesis for your next 3 upcoming meetings on Monday, 24 August 2026.
            Sourced across Calendar, Email, Drive, and GitHub.
          </p>
        </div>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.75rem",
            background: "var(--color-field, #f7f7f7)",
            padding: "0.5rem 0.875rem",
            borderRadius: "0.25rem",
            border: "1px solid var(--color-edge, #eaeaea)",
            fontSize: "0.75rem",
            fontFamily: "var(--font-mono, monospace)",
            color: "var(--color-ink-soft, #676767)"
          }}
        >
          <span
            style={{
              display: "inline-block",
              width: "8px",
              height: "8px",
              borderRadius: "50%",
              backgroundColor: "#2e7d32"
            }}
          />
          <span>Sources: 4 connected (UTC sync)</span>
          {actionCount > 0 && (
            <span
              style={{
                marginLeft: "0.5rem",
                padding: "0.15rem 0.4rem",
                borderRadius: "3px",
                background: "var(--accent-primary, #111111)",
                color: "var(--color-fill-ink, #ffffff)",
                fontSize: "0.7rem",
                fontWeight: 600
              }}
            >
              {actionCount} action{actionCount > 1 ? "s" : ""} taken
            </span>
          )}
        </div>
      </header>

      <nav
        id="region-meeting-tabs"
        data-app-region="meeting-tabs"
        aria-label="Upcoming Meetings"
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
          gap: "1rem",
          marginBottom: "1.5rem"
        }}
      >
        {MEETINGS.map((m, idx) => {
          const isSelected = idx === selectedMeetingIndex;
          return (
            <button
              key={m.id}
              onClick={() => setSelectedMeetingIndex(idx)}
              aria-pressed={isSelected}
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "flex-start",
                textAlign: "left",
                padding: "1rem",
                borderRadius: "0.25rem",
                background: "var(--color-surface, #ffffff)",
                border: isSelected
                  ? "2px solid var(--accent-primary, #111111)"
                  : "1px solid var(--color-edge, #e5e5e5)",
                cursor: "pointer",
                boxShadow: isSelected ? "0 2px 8px rgba(0,0,0,0.06)" : "none",
                transition: "all 0.15s ease",
                outline: "none"
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  width: "100%",
                  alignItems: "center",
                  marginBottom: "0.375rem"
                }}
              >
                <span
                  style={{
                    fontSize: "0.9375rem",
                    fontWeight: 600,
                    color: "var(--color-ink, #111111)"
                  }}
                >
                  {idx + 1}. {m.title}
                </span>
                <span
                  style={{
                    fontSize: "0.75rem",
                    fontWeight: 500,
                    padding: "0.15rem 0.4rem",
                    borderRadius: "3px",
                    backgroundColor: isSelected ? "var(--color-field, #f0f0f0)" : "transparent",
                    color: "var(--color-ink-soft, #676767)",
                    fontFamily: "var(--font-mono, monospace)"
                  }}
                >
                  {m.timeRange}
                </span>
              </div>

              <div
                style={{
                  fontSize: "0.75rem",
                  color: "var(--color-ink-soft, #676767)",
                  marginBottom: "0.5rem",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  width: "100%"
                }}
              >
                Attendees: {m.attendees.join(", ")}
              </div>

              <div
                style={{
                  display: "flex",
                  gap: "0.375rem",
                  flexWrap: "wrap",
                  marginTop: "auto"
                }}
              >
                <span
                  style={{
                    fontSize: "0.6875rem",
                    padding: "0.15rem 0.4rem",
                    borderRadius: "3px",
                    backgroundColor:
                      m.id === "investor-update" ? "#fff3e0" : "#e8f5e9",
                    color:
                      m.id === "investor-update" ? "#b76e00" : "#2e7d32",
                    fontWeight: 600
                  }}
                >
                  {m.id === "investor-update"
                    ? "Drive: No prior notes"
                    : m.id === "product-design"
                    ? "Drive: 24 Aug (Today)"
                    : "Drive: 12 Aug (12d)"}
                </span>
                <span
                  style={{
                    fontSize: "0.6875rem",
                    padding: "0.15rem 0.4rem",
                    borderRadius: "3px",
                    backgroundColor: "var(--color-field, #f2f2f2)",
                    color: "var(--color-ink-soft, #676767)"
                  }}
                >
                  Email: {m.id === "investor-update" ? "20 Aug (4d)" : "21 Aug (3d)"}
                </span>
              </div>
            </button>
          );
        })}
      </nav>

      {copyFeedback && (
        <div
          role="status"
          aria-live="polite"
          style={{
            marginBottom: "1rem",
            padding: "0.75rem 1rem",
            borderRadius: "0.25rem",
            backgroundColor: "#e8f5e9",
            color: "#1b5e20",
            fontSize: "0.875rem",
            fontWeight: 500,
            border: "1px solid #c8e6c9"
          }}
        >
          ✓ {copyFeedback}
        </div>
      )}

      {lastActionMessage && (
        <div
          role="status"
          aria-live="polite"
          style={{
            marginBottom: "1rem",
            padding: "0.75rem 1rem",
            borderRadius: "0.25rem",
            backgroundColor: "#e3f2fd",
            color: "#0d47a1",
            fontSize: "0.875rem",
            fontWeight: 500,
            border: "1px solid #bbdefb"
          }}
        >
          ℹ {lastActionMessage}
        </div>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))",
          gap: "1.5rem",
          alignItems: "start"
        }}
      >
        <section
          id="region-context-signals"
          data-app-region="context-signals"
          aria-labelledby="heading-context"
          style={{
            background: "var(--color-surface, #ffffff)",
            border: "1px solid var(--color-edge, #e5e5e5)",
            borderRadius: "0.25rem",
            padding: "1.25rem",
            display: "flex",
            flexDirection: "column",
            gap: "1.25rem"
          }}
        >
          <div>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                flexWrap: "wrap",
                gap: "0.5rem"
              }}
            >
              <h2
                id="heading-context"
                style={{
                  margin: 0,
                  fontSize: "1.125rem",
                  fontWeight: 600,
                  color: "var(--color-ink, #111111)"
                }}
              >
                {currentMeeting.title}
              </h2>
              <span
                style={{
                  fontSize: "0.75rem",
                  fontFamily: "var(--font-mono, monospace)",
                  padding: "0.2rem 0.5rem",
                  borderRadius: "3px",
                  backgroundColor: "var(--color-field, #f4f4f4)",
                  color: "var(--color-ink-soft, #676767)"
                }}
              >
                Status: {currentMeeting.calendarStatus}
              </span>
            </div>
            <p
              style={{
                margin: "0.25rem 0 0 0",
                fontSize: "0.8125rem",
                color: "var(--color-ink-soft, #676767)"
              }}
            >
              Time: <strong>{currentMeeting.timeRange}</strong> • Attendees:{" "}
              {currentMeeting.attendees.join(", ")}
            </p>
          </div>

          <div
            style={{
              padding: "0.875rem",
              borderRadius: "0.25rem",
              background: "var(--color-field, #f8f9fa)",
              border: "1px solid var(--color-edge, #eaeaea)"
            }}
          >
            <div
              style={{
                fontSize: "0.75rem",
                fontWeight: 600,
                color: "var(--color-ink-soft, #676767)",
                textTransform: "uppercase",
                letterSpacing: "0.04em",
                marginBottom: "0.5rem"
              }}
            >
              Source Audit Trail &amp; Age
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
                gap: "0.5rem",
                fontSize: "0.75rem",
                fontFamily: "var(--font-mono, monospace)"
              }}
            >
              <div>
                <span style={{ color: "var(--color-ink-soft, #676767)" }}>Calendar: </span>
                <span style={{ color: "var(--color-ink, #111111)", fontWeight: 500 }}>
                  {currentMeeting.sourceRecency.calendar}
                </span>
              </div>
              <div>
                <span style={{ color: "var(--color-ink-soft, #676767)" }}>Email: </span>
                <span style={{ color: "var(--color-ink, #111111)", fontWeight: 500 }}>
                  {currentMeeting.sourceRecency.email}
                </span>
              </div>
              <div>
                <span style={{ color: "var(--color-ink-soft, #676767)" }}>Drive: </span>
                <span
                  style={{
                    color:
                      currentMeeting.id === "investor-update" ? "#b76e00" : "var(--color-ink, #111111)",
                    fontWeight: 500
                  }}
                >
                  {currentMeeting.sourceRecency.drive}
                </span>
              </div>
              <div>
                <span style={{ color: "var(--color-ink-soft, #676767)" }}>GitHub: </span>
                <span style={{ color: "var(--color-ink, #111111)", fontWeight: 500 }}>
                  {currentMeeting.sourceRecency.github}
                </span>
              </div>
            </div>
          </div>

          <div
            style={{
              padding: "1rem",
              borderRadius: "0.25rem",
              background: "var(--color-surface, #ffffff)",
              border: "1px solid var(--color-edge, #e5e5e5)"
            }}
          >
            <h3
              style={{
                margin: "0 0 0.5rem 0",
                fontSize: "0.875rem",
                fontWeight: 600,
                color: "var(--color-ink, #111111)"
              }}
            >
              Prior Context &amp; Decisions
            </h3>
            <ul
              style={{
                margin: 0,
                paddingLeft: "1.125rem",
                fontSize: "0.8125rem",
                color: "var(--color-ink, #111111)",
                lineHeight: 1.5
              }}
            >
              {currentMeeting.priorContext.points.map((pt, i) => (
                <li key={i} style={{ marginBottom: "0.375rem" }}>
                  {pt}
                </li>
              ))}
            </ul>
            <div
              style={{
                marginTop: "0.5rem",
                fontSize: "0.75rem",
                color: "var(--color-ink-soft, #676767)",
                fontFamily: "var(--font-mono, monospace)"
              }}
            >
              Source: {currentMeeting.priorContext.sourceNote}
            </div>
          </div>

          <div
            style={{
              padding: "1rem",
              borderRadius: "0.25rem",
              background: "var(--color-surface, #ffffff)",
              border: "1px solid var(--color-edge, #e5e5e5)"
            }}
          >
            <h3
              style={{
                margin: "0 0 0.5rem 0",
                fontSize: "0.875rem",
                fontWeight: 600,
                color: "var(--color-ink, #111111)"
              }}
            >
              Live Engineering &amp; Operational Work
            </h3>
            <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
              {currentMeeting.liveWork.items.map((item, idx) => (
                <div
                  key={idx}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    gap: "0.5rem",
                    padding: "0.5rem 0.625rem",
                    background: "var(--color-field, #f9f9f9)",
                    borderRadius: "0.25rem",
                    border: "1px solid var(--color-edge, #ebebeb)",
                    fontSize: "0.8125rem"
                  }}
                >
                  <div>
                    <div style={{ fontWeight: 600, color: "var(--color-ink, #111111)" }}>
                      <span style={{ fontFamily: "var(--font-mono, monospace)", marginRight: "0.375rem" }}>
                        {item.id}
                      </span>
                      {item.title}
                    </div>
                    <div style={{ fontSize: "0.75rem", color: "var(--color-ink-soft, #676767)" }}>
                      {item.meta}
                    </div>
                  </div>
                  <span
                    style={{
                      fontSize: "0.6875rem",
                      fontWeight: 600,
                      padding: "0.15rem 0.4rem",
                      borderRadius: "3px",
                      backgroundColor:
                        item.statusType === "warning"
                          ? "#fff3e0"
                          : item.statusType === "success"
                          ? "#e8f5e9"
                          : "#e3f2fd",
                      color:
                        item.statusType === "warning"
                          ? "#b76e00"
                          : item.statusType === "success"
                          ? "#2e7d32"
                          : "#0d47a1",
                      whiteSpace: "nowrap"
                    }}
                  >
                    {item.status}
                  </span>
                </div>
              ))}
            </div>
            <div
              style={{
                marginTop: "0.625rem",
                fontSize: "0.75rem",
                color: "var(--color-ink-soft, #676767)",
                fontFamily: "var(--font-mono, monospace)"
              }}
            >
              {currentMeeting.liveWork.summary}
            </div>
          </div>

          <div
            style={{
              padding: "1rem",
              borderRadius: "0.25rem",
              background: currentMeeting.missingOrUncertain.alertLevel === "warning" ? "#fffbf5" : "#f8f9fa",
              border:
                currentMeeting.missingOrUncertain.alertLevel === "warning"
                  ? "1px solid #ffe0b2"
                  : "1px solid var(--color-edge, #e0e0e0)"
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.375rem",
                fontSize: "0.8125rem",
                fontWeight: 600,
                color: currentMeeting.missingOrUncertain.alertLevel === "warning" ? "#b76e00" : "var(--color-ink, #111111)",
                marginBottom: "0.5rem"
              }}
            >
              <span>⚠ Missing &amp; Uncertain Data Gaps</span>
            </div>
            <ul
              style={{
                margin: 0,
                paddingLeft: "1.125rem",
                fontSize: "0.8125rem",
                color: "var(--color-ink, #111111)",
                lineHeight: 1.45
              }}
            >
              {currentMeeting.missingOrUncertain.items.map((gap, i) => (
                <li key={i} style={{ marginBottom: "0.375rem" }}>
                  {gap}
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section
          id="region-action-points"
          data-app-region="action-points"
          data-app-role="primary-action"
          aria-labelledby="heading-points"
          style={{
            background: "var(--color-surface, #ffffff)",
            border: "1px solid var(--color-edge, #e5e5e5)",
            borderRadius: "0.25rem",
            padding: "1.25rem",
            display: "flex",
            flexDirection: "column",
            gap: "1.25rem"
          }}
        >
          <div>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                flexWrap: "wrap",
                gap: "0.5rem"
              }}
            >
              <h2
                id="heading-points"
                style={{
                  margin: 0,
                  fontSize: "1.125rem",
                  fontWeight: 600,
                  color: "var(--color-ink, #111111)"
                }}
              >
                3 Key Points to Raise
              </h2>
              <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                <label
                  style={{
                    fontSize: "0.75rem",
                    color: "var(--color-ink-soft, #676767)",
                    display: "flex",
                    alignItems: "center",
                    gap: "0.25rem",
                    cursor: "pointer"
                  }}
                >
                  <input
                    type="checkbox"
                    checked={showOnlyActionable}
                    onChange={(e) => setShowOnlyActionable(e.target.checked)}
                    aria-label="Filter unraised points"
                  />
                  Hide covered
                </label>
              </div>
            </div>
            <p
              style={{
                margin: "0.25rem 0 0 0",
                fontSize: "0.8125rem",
                color: "var(--color-ink-soft, #676767)"
              }}
            >
              Action-oriented talking points rewritten from raw connector notes into concise decision language.
            </p>
          </div>

          <div style={{ display: "flex", gap: "0.5rem" }}>
            <input
              type="text"
              placeholder="Search points, topics, or sources..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              aria-label="Search talking points"
              style={{
                flex: 1,
                padding: "0.5rem 0.75rem",
                borderRadius: "0.25rem",
                border: "1px solid var(--color-edge, #d1d1d1)",
                background: "var(--color-field, #ffffff)",
                color: "var(--color-ink, #111111)",
                fontSize: "0.8125rem",
                outline: "none"
              }}
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery("")}
                aria-label="Clear search"
                style={{
                  padding: "0.5rem 0.75rem",
                  borderRadius: "0.25rem",
                  border: "1px solid var(--color-edge, #d1d1d1)",
                  background: "var(--color-surface, #ffffff)",
                  color: "var(--color-ink, #111111)",
                  fontSize: "0.75rem",
                  cursor: "pointer"
                }}
              >
                Clear
              </button>
            )}
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "0.875rem" }}>
            {filteredPoints.length === 0 ? (
              <div
                style={{
                  padding: "1.5rem",
                  textAlign: "center",
                  fontSize: "0.8125rem",
                  color: "var(--color-ink-soft, #676767)",
                  background: "var(--color-field, #f9f9f9)",
                  borderRadius: "0.25rem"
                }}
              >
                No points match current filter criteria.
              </div>
            ) : (
              filteredPoints.map((point) => {
                const pointKey = `${currentMeeting.id}-${point.id}`;
                const isChecked = Boolean(checkedPoints[pointKey]);
                return (
                  <div
                    key={point.id}
                    style={{
                      padding: "1rem",
                      borderRadius: "0.25rem",
                      background: isChecked ? "var(--color-field, #fcfcfc)" : "var(--color-surface, #ffffff)",
                      border: isChecked
                        ? "1px solid var(--color-edge, #e0e0e0)"
                        : "1px solid var(--color-edge, #d9d9d9)",
                      opacity: isChecked ? 0.75 : 1,
                      transition: "all 0.15s ease"
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        alignItems: "flex-start",
                        gap: "0.625rem"
                      }}
                    >
                      <input
                        type="checkbox"
                        id={`check-${pointKey}`}
                        checked={isChecked}
                        onChange={() => handleTogglePoint(currentMeeting.id, point.id)}
                        aria-label={`Mark point ${point.id} as covered`}
                        style={{ marginTop: "0.2rem", cursor: "pointer" }}
                      />
                      <div style={{ flex: 1 }}>
                        <label
                          htmlFor={`check-${pointKey}`}
                          style={{
                            fontSize: "0.875rem",
                            fontWeight: 600,
                            color: "var(--color-ink, #111111)",
                            cursor: "pointer",
                            textDecoration: isChecked ? "line-through" : "none",
                            display: "block",
                            marginBottom: "0.25rem"
                          }}
                        >
                          {point.id}. {point.title}
                        </label>
                        <div
                          style={{
                            fontSize: "0.8125rem",
                            color: "var(--color-ink, #111111)",
                            lineHeight: 1.45,
                            marginBottom: "0.375rem"
                          }}
                        >
                          {point.detail}
                        </div>
                        <div
                          style={{
                            fontSize: "0.75rem",
                            color: "var(--color-ink-soft, #676767)",
                            fontStyle: "normal",
                            marginBottom: "0.375rem"
                          }}
                        >
                          <strong>Why it matters:</strong> {point.rationale}
                        </div>
                        <div
                          style={{
                            fontSize: "0.6875rem",
                            color: "var(--color-ink-soft, #676767)",
                            fontFamily: "var(--font-mono, monospace)"
                          }}
                        >
                          Source: {point.source}
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          <div
            style={{
              marginTop: "0.5rem",
              padding: "1rem",
              borderRadius: "0.25rem",
              background: "var(--color-field, #f9f9f9)",
              border: "1px solid var(--color-edge, #eaeaea)"
            }}
          >
            <div
              style={{
                fontSize: "0.8125rem",
                fontWeight: 600,
                color: "var(--color-ink, #111111)",
                marginBottom: "0.75rem"
              }}
            >
              Prepared Meeting Actions &amp; Tools
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "0.625rem" }}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: "0.5rem",
                  flexWrap: "wrap"
                }}
              >
                <span style={{ fontSize: "0.8125rem", color: "var(--color-ink, #111111)" }}>
                  Copy complete brief summary to clipboard
                </span>
                <button
                  onClick={handleCopyBrief}
                  aria-label="Copy meeting brief"
                  style={{
                    padding: "0.5rem 1rem",
                    borderRadius: "0.25rem",
                    backgroundColor: "var(--accent-primary, #111111)",
                    color: "var(--color-fill-ink, #ffffff)",
                    border: "none",
                    fontWeight: 500,
                    fontSize: "0.8125rem",
                    cursor: "pointer",
                    transition: "opacity 0.15s ease"
                  }}
                >
                  Copy Brief
                </button>
              </div>

              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: "0.5rem",
                  flexWrap: "wrap",
                  borderTop: "1px solid var(--color-edge, #ececec)",
                  paddingTop: "0.625rem"
                }}
              >
                <span style={{ fontSize: "0.8125rem", color: "var(--color-ink, #111111)" }}>
                  Stage Issue #900: Support runbook for Priya
                </span>
                <button
                  onClick={() =>
                    handleExecutePreparedAction(
                      "Support Runbook Task",
                      "Created Issue #900 assigned to Priya for 28 August delivery."
                    )
                  }
                  aria-label="Stage support runbook task issue 900"
                  style={{
                    padding: "0.45rem 0.875rem",
                    borderRadius: "0.25rem",
                    backgroundColor: "var(--color-surface, #ffffff)",
                    color: "var(--color-ink, #111111)",
                    border: "1px solid var(--color-edge, #d1d1d1)",
                    fontWeight: 500,
                    fontSize: "0.75rem",
                    cursor: "pointer"
                  }}
                >
                  Stage Issue (#900)
                </button>
              </div>

              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: "0.5rem",
                  flexWrap: "wrap",
                  borderTop: "1px solid var(--color-edge, #ececec)",
                  paddingTop: "0.625rem"
                }}
              >
                <span style={{ fontSize: "0.8125rem", color: "var(--color-ink, #111111)" }}>
                  Stage Issue #521: Assign webhook retries to Alex
                </span>
                <button
                  onClick={() =>
                    handleExecutePreparedAction(
                      "Issue Assignment",
                      "Assigned webhook delivery retries (#521) to Alex."
                    )
                  }
                  aria-label="Assign issue 521 to Alex"
                  style={{
                    padding: "0.45rem 0.875rem",
                    borderRadius: "0.25rem",
                    backgroundColor: "var(--color-surface, #ffffff)",
                    color: "var(--color-ink, #111111)",
                    border: "1px solid var(--color-edge, #d1d1d1)",
                    fontWeight: 500,
                    fontSize: "0.75rem",
                    cursor: "pointer"
                  }}
                >
                  Assign Issue (#521)
                </button>
              </div>
            </div>
          </div>

          <div
            style={{
              fontSize: "0.75rem",
              color: "var(--color-ink-soft, #676767)",
              lineHeight: 1.4
            }}
          >
            Connected sources: <strong>Google Calendar</strong> • <strong>Email (evalco.test)</strong> • <strong>Drive</strong> • <strong>GitHub (evalco/app)</strong>
          </div>
        </section>
      </div>
    </div>
  );
}

mountApp(document.getElementById("root")!, () => <App />);
