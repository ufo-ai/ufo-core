#!/bin/bash
set -euo pipefail

if [ "$#" -ne 5 ]; then
  echo "usage: billing_export_plan_gate.sh <namespace> <env> <image-tag> <git-sha> <run-url>" >&2
  exit 2
fi

NAMESPACE="$1"
DEPLOY_ENV="$2"
IMAGE_TAG="$3"
GIT_SHA="$4"
RUN_URL="$5"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESULT="$(mktemp)"
trap 'rm -f "$RESULT"' EXIT

case "$DEPLOY_ENV" in
  testing|prod) ;;
  *) echo "invalid deploy environment" >&2; exit 2 ;;
esac

test -n "${DD_API_KEY:-}" || { echo "DD_API_KEY is unset" >&2; exit 2; }
test -n "${DD_APP_KEY:-}" || { echo "DD_APP_KEY is unset" >&2; exit 2; }

DEPLOY_IMAGE="$(kubectl --namespace "$NAMESPACE" get deployment/ufo-serve -o json \
  | jq -er '.spec.template.spec.containers[] | select(.name == "serve") | .image')"
POD="$(kubectl --namespace "$NAMESPACE" get pods -l app=ufo-serve -o json \
  | jq -er --arg image "$DEPLOY_IMAGE" '
      [.items[]
       | select(.status.phase == "Running")
       | select(any(.spec.containers[]; .name == "serve" and .image == $image))
       | select(any(.status.containerStatuses[]; .name == "serve" and .ready == true))]
      | sort_by(.metadata.creationTimestamp)
      | last
      | .metadata.name')"

set +e
kubectl --namespace "$NAMESPACE" exec -i "$POD" -c serve -- \
  env UFO_CONFIG=/app/ufo.toml python - \
  < "$SCRIPT_DIR/billing_export_plan_check.py" > "$RESULT"
PROBE_STATUS="$?"
set -e

if ! jq -e '
  .status as $status
  | ($status == "ok" or $status == "error")
  and (.db_role | type == "string")
  and (.relation == "ledger_export")
  and (.index | type == "string")
  and (.access_method | type == "string")
  and (.execution_ms | type == "number")
  and (.threshold_ms == 10)
  and (.seq_scan | type == "boolean")
  and (.actual_export_rows | type == "number")
  and (.query_sha256 | type == "string")
  and (.failures | type == "array")' "$RESULT" >/dev/null; then
  PROBE_STATUS=1
  jq -n '{
    status: "error",
    db_role: "unverified",
    relation: "ledger_export",
    index: "none",
    access_method: "unavailable",
    execution_ms: 0,
    threshold_ms: 10,
    seq_scan: false,
    actual_export_rows: 0,
    query_sha256: "",
    failures: ["probe did not return a valid result"]
  }' > "$RESULT"
fi

EVENT_STATUS="$(jq -r '.status' "$RESULT")"
if [ "$EVENT_STATUS" = "ok" ]; then
  TITLE="ufo billing usage-export plan verified"
  MESSAGE="The live billing usage-export query used the required index under the application role."
else
  TITLE="ufo billing usage-export plan failed"
  MESSAGE="The live billing usage-export query did not meet its plan contract."
  PROBE_STATUS=1
fi

PAYLOAD="$(jq -n \
  --arg env "$DEPLOY_ENV" \
  --arg git_sha "$GIT_SHA" \
  --arg image_tag "$IMAGE_TAG" \
  --arg message "$MESSAGE" \
  --arg run_url "$RUN_URL" \
  --arg title "$TITLE" \
  --slurpfile result "$RESULT" '
  $result[0] as $plan
  | {
      data: {
        type: "event",
        attributes: {
          category: "alert",
          integration_id: "custom-events",
          title: $title,
          message: $message,
          host: "github-actions",
          tags: [
            "env:" + $env,
            "service:ufo",
            "check:billing_usage_export_plan",
            "db_role:" + $plan.db_role,
            "relation:" + $plan.relation,
            "index:" + $plan.index,
            "access_method:" + $plan.access_method
          ],
          attributes: {
            status: $plan.status,
            custom: ($plan + {
              git_sha: $git_sha,
              image_tag: $image_tag,
              run_url: $run_url
            })
          }
        }
      }
    }')"

curl -sS --fail-with-body -X POST \
  "https://event-management-intake.us5.datadoghq.com/api/v2/events" \
  -H "DD-API-KEY: $DD_API_KEY" \
  -H "DD-APPLICATION-KEY: $DD_APP_KEY" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD"

exit "$PROBE_STATUS"
