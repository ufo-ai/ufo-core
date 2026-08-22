#!/usr/bin/env bash
# Rolled out = the deployment's newest ReplicaSet is fully available. Old pods drain for up to
# terminationGracePeriodSeconds (700s on the turn-holding workloads) after that; the gate never
# waits on a drain.
set -euo pipefail

NAMESPACE=$1
shift
DEADLINE=$((SECONDS + 900))

get() {
  kubectl --namespace "$NAMESPACE" get "$@"
}

tick() {
  if ((SECONDS >= DEADLINE)); then
    echo "await_rollout: $1" >&2
    exit 1
  fi
  sleep 5
}

for name in "$@"; do
  generation=$(get "deployment/$name" -o jsonpath='{.metadata.generation}')
  until [[ "$(get "deployment/$name" -o jsonpath='{.status.observedGeneration}')" -ge "$generation" ]]; do
    tick "deployment/$name never observed generation $generation"
  done

  revision=$(get "deployment/$name" -o jsonpath='{.metadata.annotations.deployment\.kubernetes\.io/revision}')
  replicas=$(get "deployment/$name" -o jsonpath='{.spec.replicas}')

  newest_rs=""
  while [[ -z "$newest_rs" ]]; do
    newest_rs=$(get replicasets -o json | jq -r --arg name "$name" --arg revision "$revision" '
      [.items[]
       | select(any(.metadata.ownerReferences[]?; .kind == "Deployment" and .name == $name))
       | select(.metadata.annotations["deployment.kubernetes.io/revision"] == $revision)
       | .metadata.name][0] // empty')
    [[ -n "$newest_rs" ]] || tick "deployment/$name has no ReplicaSet at revision $revision"
  done

  until [[ "$(get "replicaset/$newest_rs" -o jsonpath='{.status.availableReplicas}')" == "$replicas" ]]; do
    tick "replicaset/$newest_rs never reached $replicas available replicas"
  done
  echo "deployment/$name rolled out: replicaset/$newest_rs has $replicas replicas available"
done
