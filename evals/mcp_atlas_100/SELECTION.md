# MCP-Atlas 100 selection

Source: `MCP-Atlas.parquet`, SHA-256
`2d7bc052f14cbcb3b8294293481053f7111d256f9c9deaa96f3ff632d19958d0`. The reference checkout is
commit `d01e9b590e931f877bc01565ab57d191603561f6`.

The source audit finds 500 tasks, 36 reference-used MCPs, 220 enabled tools, and 106
reference-used tools. Applying the executable profile leaves 111 tasks across 21 MCPs, with 100
enabled tools and 57 reference-used tools. The selected 100 tasks retain all 21 MCPs, all 100
enabled tools, all 57 reference-used tools, and 479 reference calls.

This is the 21-MCP approved executable selection. Its results are not score-comparable with a
36-MCP, 220-tool catalog candidate.

## Profile

`profile.py` is the executable boundary: every allowed tool has one allowed MCP mapping. A task is
eligible only when every reference tool is allowed and every reference tool appears in its filtered
enabled set. Unlisted MCPs, unlisted tools on approved MCPs, and mismatched tool-to-MCP mappings fail
validation.

The credentialed route contains E2B `run_code`, Exa web search, and GitHub read operations. The
secretless route contains public APIs and local read/query/computation tools. Desktop Commander is
excluded because its tool calls emit telemetry. Removing those tasks leaves MCP Server Code Runner
without an eligible task, so it is absent. CLI commands are limited to option-free `ls`, `cat`, or
`find`; relative paths resolve under `/data`, absolute paths must descend from `/data`, and `..` is
rejected.

## Method

```sh
uv run --with pyarrow python evals/mcp_atlas_100/select.py \
  /tmp/MCP-Atlas.parquet --output evals/mcp_atlas_100/data.json
```

Selection is deterministic:

1. Greedy weighted set cover selects tasks until all 57 reference-used tools are covered. Rarer
   tools receive more weight; ties prefer enabled-tool coverage, difficulty, then task ID.
2. Further tasks maximize uncovered enabled tools until all 100 are covered.
3. Remaining slots take the highest difficulty score, then task ID.
4. One bounded equal-call-count swap may improve duplicated reference-tool coverage without losing
   reference or enabled coverage.

Difficulty is
`100*calls + 50*(servers-1) + 20*distinct_tools + 10*hard_calls +
25*changed_argument_requeries + 100*parallel_calls`.

The output begins with this intentional smoke prefix, then sorts remaining tasks by task ID:

1. `68925f01db31fe35560e3eb5`: CLI, E2B, Exa
2. `689f4d693e212e8ef3390731`: Fetch, GitHub, MCP Code Executor, Whois
3. `6896416f7b30e5d8ccd7c8f7`: Filesystem, Git, Met Museum

Together they exercise ten MCPs and all three credentialed services. The smoke CLI reference
commands are bare `ls` and `cat "Top Movies.csv"`.

## Fidelity

- `prompt` preserves `PROMPT`; `claims` preserves the strings decoded from `GTFA_CLAIMS`.
- `trajectory_tools` preserves distinct reference-called tools in first-call order.
- `tool_servers` explicitly maps every enabled or reference tool.
- `data.json` contains the sole benchmark shape used by selection, loading, execution, and scoring.

Dataset SHA-256:
`33afb7a18e632f2ca1618106c8c52b0b4e1854f1d1cfb64e779bf4ef98d5cb0d`.
