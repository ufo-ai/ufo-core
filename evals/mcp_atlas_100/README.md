# MCP-Atlas 100

This suite runs 100 tasks from the MCP-Atlas public dataset. It grades each final answer claim as
`fulfilled=1`, `partial=0.5`, or `unfulfilled=0`; a task passes at mean claim coverage `>=0.75`.
The claim judge is pinned to `gpt-5.4-mini`.
The report assigns Top `[78%, 100%]`, Mid `[65%, 78%)`, or Tail `[0%, 65%)` performance from the
task pass rate.

The executable profile contains 21 MCPs and 100 explicitly allowed tools. It is not
score-comparable with a 36-MCP, 220-tool catalog evaluation.

| Route | MCPs |
|---|---|
| Secretless public/local | `arxiv`, `calculator`, `cli-mcp-server`, `clinicaltrialsgov-mcp-server`, `context7`, `ddg-search`, `fetch`, `filesystem`, `git`, `mcp-code-executor`, `memory`, `met-museum`, `open-library`, `osm-mcp-server`, `pubmed`, `weather`, `whois`, `wikipedia` |
| Credentialed external | `e2b-server`, `exa`, `github` read operations |

The allowlist rejects every unlisted tool, including new tools added to an approved MCP. Slack,
Google Workspace, Airtable, Alchemy, Brave Search, Google Maps, Lara Translate, MongoDB, National
Parks, Notion, Oxylabs, Twelve Data, Weather Data, Desktop Commander, and MCP Server Code Runner are
excluded. Desktop Commander emits telemetry from tool calls. The CLI MCP accepts only `ls`, `cat`,
and `find`, without options; paths resolve under `/data` and cannot contain `..`. E2B `run_code` is
credentialed. MCP Code Executor remains secretless.

Preflight validates the profile and selected schemas without invoking tools. The target lists and
calls public/local tools only through the secretless endpoint, and E2B, Exa, or GitHub tools only
through the credentialed endpoint. The first three cases are the live readiness smoke and cover all
three credentialed services plus seven public/local MCPs.

```sh
IMAGE=ghcr.io/scaleapi/mcp-atlas@sha256:62f5c3f208bee0790b3399f73203b622c434cd54a0adf946961cfbd81c196289
PUBLIC_SERVERS=arxiv,calculator,cli-mcp-server,clinicaltrialsgov-mcp-server,context7,ddg-search,fetch,filesystem,git,mcp-code-executor,memory,met-museum,open-library,osm-mcp-server,pubmed,weather,whois,wikipedia
EXTERNAL_SERVERS=e2b-server,exa,github

docker run -d --name mcp-atlas-public -p 127.0.0.1:1984:1984 \
  -e ENABLED_SERVERS="$PUBLIC_SERVERS" "$IMAGE"
docker run -d --name mcp-atlas-external -p 127.0.0.1:1985:1984 \
  -e ENABLED_SERVERS="$EXTERNAL_SERVERS" --env-file .env "$IMAGE"

for port in 1984 1985; do
  for attempt in $(seq 1 60); do
    curl --fail --silent --max-time 2 -X POST "http://127.0.0.1:$port/list-tools" \
      >/dev/null && break
    test "$attempt" -lt 60 || exit 1
    sleep 1
  done
done

uv run python -m evals --only mcp_atlas_100 \
  --mcp-atlas-url http://localhost:1984 \
  --mcp-atlas-external-url http://localhost:1985 \
  --mcp-atlas-samples 3

uv run python -m evals --only mcp_atlas_100 \
  --mcp-atlas-url http://localhost:1984 \
  --mcp-atlas-external-url http://localhost:1985

docker rm -f mcp-atlas-public mcp-atlas-external
```

Use dedicated containers and discard them after the run. The external `.env` contains only
`E2B_API_KEY`, `EXA_API_KEY`, and a public-repository, read-only `GITHUB_TOKEN`. The image digest
pins the Atlas base; its Git-sourced GitHub, PubMed, and Weather MCP packages resolve upstream at
container startup, so record the run timestamp and resolved revisions with results.

Sources: [paper](https://arxiv.org/abs/2602.00933),
[official harness](https://github.com/scaleapi/mcp-atlas), and
[public dataset](https://huggingface.co/datasets/ScaleAI/MCP-Atlas). The dataset is CC BY 4.0.
