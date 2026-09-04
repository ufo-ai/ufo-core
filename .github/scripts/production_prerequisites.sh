#!/usr/bin/env bash

set -euo pipefail

check_headroom() {
  QUOTA=$(aws service-quotas get-service-quota --service-code "$1" --quota-code "$2" --query Quota.Value --output text)
  printf '%s %s %s %s\n' "$1" "$2" "$3" "$4"
  awk -v quota="$QUOTA" -v used="$4" -v required="$3" 'BEGIN { exit !(quota - used >= required) }'
}

missing() {
  awk -v desired="$1" -v owned="$2" 'BEGIN { value = desired - owned; if (value < 0) value = 0; print value }'
}

standard_vcpus() {
  COUNTS=$(jq -r '[.[] | select(.[1] == null) | .[0] | select(test("^(?:[acdhmrtz][0-9]|i(?:[0-9]|m[0-9]|s[0-9]))"))] | group_by(.)[] | [length, .[0]] | @tsv' <<< "$1") || return
  TOTAL=0
  while read -r COUNT INSTANCE_TYPE; do
    test -n "$INSTANCE_TYPE" || continue
    DEFAULT_VCPUS=$(aws ec2 describe-instance-types --instance-types "$INSTANCE_TYPE" --query 'InstanceTypes[0].VCpuInfo.DefaultVCpus' --output text) || return
    TOTAL=$((TOTAL + COUNT * DEFAULT_VCPUS))
  done <<< "$COUNTS"
  printf '%s\n' "$TOTAL"
}

nat_zones() {
  for SUBNET in $1; do
    SUBNET_ZONE=$(aws ec2 describe-subnets --subnet-ids "$SUBNET" --query 'Subnets[0].AvailabilityZone' --output text) || return
    printf '%s\n' "$SUBNET_ZONE"
  done
}

test "$AWS_REGION" = us-east-1
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
test "$ACCOUNT" = 899147036157
SES_ACCOUNT=$(aws sesv2 get-account --query '[ProductionAccessEnabled, SendingEnabled]' --output text)
read -r SES_ACCESS SES_SENDING <<< "$SES_ACCOUNT"
test "$SES_ACCESS" = True
test "$SES_SENDING" = True

USED=$(aws ec2 describe-vpcs --query 'length(Vpcs)' --output json)
OWNED=$(aws ec2 describe-vpcs --filters Name=tag:flyingobject.ai/environment,Values=prod --query 'length(Vpcs)' --output json)
REQUIRED=$(missing 1 "$OWNED")
check_headroom vpc L-F678F1CE "$REQUIRED" "$USED"
USED=$(aws ec2 describe-addresses --query 'length(Addresses)' --output json)
OWNED=$(aws ec2 describe-addresses --filters Name=tag:flyingobject.ai/environment,Values=prod --query 'length(Addresses)' --output json)
REQUIRED=$(missing 3 "$OWNED")
check_headroom ec2 L-0263D0A3 "$REQUIRED" "$USED"
USED=$(aws eks list-clusters --query 'length(clusters)' --output json)
OWNED=$(aws eks list-clusters --query 'length(clusters[?@ == `prod-cluster`])' --output json)
REQUIRED=$(missing 1 "$OWNED")
check_headroom eks L-1194D53C "$REQUIRED" "$USED"
USED=$(aws rds describe-db-instances --query 'length(DBInstances)' --output json)
OWNED=$(aws rds describe-db-instances --query 'length(DBInstances[?DBInstanceIdentifier == `prod-postgres`])' --output json)
REQUIRED=$(missing 1 "$OWNED")
check_headroom rds L-7B6409FD "$REQUIRED" "$USED"
USED=$(aws elasticache describe-cache-clusters --query 'sum(CacheClusters[].NumCacheNodes)' --output json)
OWNED=$(aws elasticache describe-cache-clusters --query 'sum(CacheClusters[?starts_with(CacheClusterId, `prod-redis-`)].NumCacheNodes)' --output json)
REQUIRED=$(missing 2 "$OWNED")
check_headroom elasticache L-DFE45DF3 "$REQUIRED" "$USED"
INSTANCES=$(aws ec2 describe-instances --filters Name=instance-state-name,Values=pending,running,shutting-down --query 'Reservations[].Instances[].[InstanceType,InstanceLifecycle]' --output json)
USED=$(standard_vcpus "$INSTANCES")
PROD_INSTANCES=$(aws ec2 describe-instances --filters Name=instance-state-name,Values=pending,running Name=tag:aws:eks:cluster-name,Values=prod-cluster --query 'Reservations[].Instances[].[InstanceType,InstanceLifecycle]' --output json)
OWNED=$(standard_vcpus "$PROD_INSTANCES")
REQUIRED=$(missing 56 "$OWNED")
check_headroom ec2 L-1216C47A "$REQUIRED" "$USED"
USED=$(aws elbv2 describe-load-balancers --query 'length(LoadBalancers[?Type == `network`])' --output json)
OWNED=$(aws resourcegroupstaggingapi get-resources --resource-type-filters elasticloadbalancing:loadbalancer --tag-filters Key=elbv2.k8s.aws/cluster,Values=prod-cluster --query 'length(ResourceTagMappingList[?contains(ResourceARN, `:loadbalancer/net/`)])' --output json)
REQUIRED=$(missing 2 "$OWNED")
check_headroom elasticloadbalancing L-69A177A2 "$REQUIRED" "$USED"

NAT_SUBNETS=$(aws ec2 describe-nat-gateways --filter Name=state,Values=pending,available,deleting --query 'NatGateways[].SubnetId' --output text)
PROD_NAT_SUBNETS=$(aws ec2 describe-nat-gateways --filter Name=state,Values=pending,available Name=tag:flyingobject.ai/environment,Values=prod --query 'NatGateways[].SubnetId' --output text)
NAT_ZONES=$(nat_zones "$NAT_SUBNETS")
PROD_NAT_ZONES=$(nat_zones "$PROD_NAT_SUBNETS")
ZONES=$(aws ec2 describe-availability-zones --filters Name=state,Values=available --query 'AvailabilityZones[:3].ZoneName' --output text)
for ZONE in $ZONES; do
  USED=$(awk -v zone="$ZONE" '$0 == zone { count++ } END { print count + 0 }' <<< "$NAT_ZONES")
  OWNED=$(awk -v zone="$ZONE" '$0 == zone { count++ } END { print count + 0 }' <<< "$PROD_NAT_ZONES")
  REQUIRED=$(missing 1 "$OWNED")
  check_headroom vpc L-FE5A380F "$REQUIRED" "$USED"
done
