#!/usr/bin/env bash
set -euo pipefail

NAMESPACE=$1
shift
DEADLINE=$((SECONDS + 900))

namespace=$(kubectl get "namespace/$NAMESPACE" --ignore-not-found -o name)
if [[ -z "$namespace" ]]; then
  exit 0
fi

for name in "$@"; do
  while true; do
    draining=$(kubectl --namespace "$NAMESPACE" get pods -l "app=$name" -o json | jq -r '
      [.items[] | select(.metadata.deletionTimestamp != null)] | length')
    [[ "$draining" == 0 ]] && break
    if ((SECONDS >= DEADLINE)); then
      echo "await_drained: deployment/$name still has $draining terminating pods" >&2
      exit 1
    fi
    sleep 5
  done
  echo "deployment/$name has no terminating pods"
done
