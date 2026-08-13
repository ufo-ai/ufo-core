# Usage views

Usage has two scopes. An agent page shows one agent and all work that it delegates. The workspace
page shows all agents and workspace jobs. Every value uses UTC ledger time.

## Shared controls

The range control has `7 days`, `30 days`, `90 days`, and `All time`. The default is `30 days`.
The response contains one UTC bucket for every day in the selected range, including zero days, and
an all-time total. The UI does not sum buckets to make the all-time value.

The selected range controls all tables and the history chart. Caps keep their own rolling windows.

## Agent usage

```text
Usage                                  [7 days] [30 days] [90 days] [All time]

Tokens                 All-time tokens       Daily average       Blended cost
98.3M                  1.02B                 3.3M                $1.88/Mtok
Last 30 days · +12%    Since Mar 4           24 active days      Last 30 days

Daily usage
Tokens┤                         ╭─╮
      ┤          ╭╮      ╭──╮  │ │
      ┼──────────╯╰──────╯  ╰──╯ ╰──
       Aug 1                         Aug 30

Execution
Execution              Tokens       Token share    Cost        $/Mtok
This agent             6.1M         6.2%           $98.11      $16.08
Research subagent      90.8M        92.4%          $83.04      $0.91
Browser subagent       1.4M         1.4%           $3.57       $2.55
Total                  98.3M        100.0%          $184.72     $1.88

Models
Model                  Tokens       Token share    Cost          $/Mtok
claude-opus-4-8        7.4M         7.5%           $101.68       $13.74
gpt-5.6-terra          90.9M        92.5%          $83.04        $0.91

Caps
Window                 Limit              On breach
24h                    $100.00            Suspend the turn
```

`This agent` means turns with no `subagent_profile`. Each other row names one subagent profile.
Nested subagents stay under their own profile. A selected agent owns all rows because every child
turn has the selected agent ID.

## Workspace usage

The workspace page uses the same range control, figures, daily chart, models, and caps.
It replaces `Execution` with these tables:

```text
Agents
Agent                  Tokens       Token share    Cost        $/Mtok
Main                   61.2M        62.3%          $121.40     $1.98
Support                27.8M        28.3%          $49.82      $1.79
Research               9.3M         9.4%           $13.50      $1.45
Workspace jobs         42K          <0.1%          $0.08       $1.90
Total                  98.3M        100.0%          $184.80     $1.88

Delegation
Execution              Tokens       Token share    Cost        $/Mtok
Agents                 14.0M        14.2%          $104.36     $7.45
Subagents              84.3M        85.8%          $80.36      $0.95
Total                  98.3M        100.0%          $184.72     $1.88

Members
Member                 Tokens       Token share    Cost          $/Mtok
alex@work.com          54.2M        55.1%          $101.23       $1.87
sam@work.com           44.1M        44.9%          $83.49        $1.89
```

An agent row includes the direct and delegated work of that agent. This rule makes the agent rows
sum to the workspace turn total. `Workspace jobs` holds ledger rows with no turn. It does not
appear in member or delegation tables.

Select an agent row to open its existing Usage tab with the same date range.

## Calculations

Only `tokens` and `sandbox_tokens` rows enter token measures. Images, videos, and egress enter
spend totals and get their own `Other usage` table when present.

| Measure | Calculation |
|---|---|
| Token share | row token count / selected token count |
| Blended cost | token cost / token count × 1,000,000 |

The totals use integer micro-USD and token counts. The UI rounds only formatted values. A zero
token count shows `—` for blended cost.

## Read projection

One usage response serves both pages. The scope is `workspace`, `member`, or `agent`. An agent
scope contains delegated turns. A member scope contains the member's root turns and all descendants
of those turns. The existing audience and admin gates stay at the route boundary.

```text
window_seconds
usage
  selected
  all_time
  first_used_at
  previous_tokens
  daily[]
  by_execution[]
  by_model[]
by_agent[]
by_member[]
by_dimension[]
caps[]
```

Each total and daily bucket has `tokens`, `token_micro_usd`, and `total_micro_usd`. The client
computes display shares and blended cost from response integers. `window_seconds` is null for `All
time`.

## Query support

The current ledger directly provides daily totals, all-time totals, agent shares, subagent shares,
model shares, and blended cost. The view does not reconstruct token classes from aggregate fields.

The ledger has a workspace and time index. Current query measurements do not require a turn and
time index.

## Useful signals

Ship these with the requested measures:

- Token change against the previous equal range. Do not show it for `All time`.
- Model token share and blended cost. These show routing effects.

Do not add forecasts, budgets, alerts, or provider invoice claims. The ledger is measured product
usage. It is not the provider invoice.
