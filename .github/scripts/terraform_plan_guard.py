#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

FLAG_TOMBSTONES = frozenset(
    json.loads((Path(__file__).parents[2] / "infra" / "flag_tombstones.json").read_text())
)
# The resources this repo has retired. An address here is one the deploy is permitted to destroy,
# and one no root may declare again — the contract test holds the second half, so the record cannot
# outlive its job by quietly permitting a deletion nobody meant.
RETIRED_RESOURCES = frozenset(
    json.loads((Path(__file__).parents[2] / "infra" / "retired_resources.json").read_text())
)
FLAG_RESOURCE_TYPE = "cloudflare_flagship_flag"
FLAG_ENVIRONMENTS = ("testing", "prod")

REGENERABLE_RESOURCE_TYPES = frozenset(
    {
        "aws_acm_certificate",
        "aws_acm_certificate_validation",
        "aws_ecr_lifecycle_policy",
        "aws_elasticache_subnet_group",
        "aws_s3_bucket_server_side_encryption_configuration",
        "aws_secretsmanager_secret_version",
        "aws_security_group",
        "aws_security_group_rule",
        "cloudflare_ruleset",
        "cloudflare_workers_route",
        "cloudflare_workers_script",
        "cloudflare_zone_setting",
        "datadog_dashboard",
        "datadog_metric_metadata",
        "datadog_metric_tag_configuration",
        "datadog_monitor",
        "helm_release",
        "kubectl_manifest",
        "kubernetes_namespace_v1",
        "kubernetes_secret",
        "kubernetes_secret_v1",
        "tls_self_signed_cert",
    }
)
REGENERABLE_MODULE_RESOURCE_TYPES: dict[str, frozenset[str]] = {
    "module.platform": frozenset({"cloudflare_dns_record"}),
    "module.platform.module.eks": frozenset(
        {
            "aws_ec2_tag",
            "aws_eks_addon",
            "aws_eks_access_entry",
            "aws_eks_access_policy_association",
            "aws_eks_node_group",
            "aws_iam_role_policy_attachment",
            "aws_kms_alias",
            "aws_launch_template",
            "null_resource",
            "time_sleep",
        }
    ),
    "module.platform.module.irsa_app_s3": frozenset({"aws_iam_role_policy_attachment"}),
    "module.platform.module.irsa_external_secrets": frozenset({"aws_iam_role_policy_attachment"}),
    "module.platform.module.irsa_gateway_ses": frozenset({"aws_iam_role_policy_attachment"}),
    "module.platform.module.irsa_lb_controller": frozenset({"aws_iam_role_policy_attachment"}),
    "module.platform.module.rds": frozenset(
        {
            "aws_db_parameter_group",
            "aws_db_subnet_group",
        }
    ),
    "module.platform.module.vpc": frozenset(
        {
            "aws_default_network_acl",
            "aws_default_route_table",
            "aws_default_security_group",
            "aws_eip",
            "aws_internet_gateway",
            "aws_route",
            "aws_route_table",
            "aws_route_table_association",
        }
    ),
}


def _is_regenerable(address: str, resource_type: str) -> bool:
    if resource_type in REGENERABLE_RESOURCE_TYPES:
        return True
    return any(
        address.startswith(f"{prefix}.") and resource_type in resource_types
        for prefix, resource_types in REGENERABLE_MODULE_RESOURCE_TYPES.items()
    )


def _is_tombstoned_flag(
    address: str, resource_type: str, actions: list[str], before: object
) -> bool:
    if resource_type != FLAG_RESOURCE_TYPE or actions != ["delete"] or not isinstance(before, dict):
        return False
    flag_key = before.get("flag_key")
    return (
        isinstance(flag_key, str)
        and flag_key in FLAG_TOMBSTONES
        and address
        in {
            f'{FLAG_RESOURCE_TYPE}.{environment}_portal["{flag_key}"]'
            for environment in FLAG_ENVIRONMENTS
        }
    )


def _is_retired(address: str, actions: list[str]) -> bool:
    """A retirement destroys once. A replace is never one, so `delete/create` stays refused."""
    return actions == ["delete"] and address in RETIRED_RESOURCES


def rejected_deletions(plan: object) -> list[str]:
    match plan:
        case {"resource_changes": list(changes)}:
            pass
        case {"resource_changes": _}:
            raise ValueError("Terraform plan JSON has an invalid resource_changes value")
        case {"format_version": str()}:
            changes = []
        case _:
            raise ValueError("Terraform plan JSON has no resource_changes list")

    rejected = []
    for resource in changes:
        match resource:
            case {
                "address": str(address),
                "type": str(resource_type),
                "change": {"actions": list(actions)} as change,
            }:
                pass
            case _:
                raise ValueError("Terraform resource change has an invalid shape")
        if any(type(action) is not str for action in actions):
            raise ValueError("Terraform resource actions must be strings")
        if (
            "delete" in actions
            and not _is_regenerable(address, resource_type)
            and not _is_tombstoned_flag(address, resource_type, actions, change.get("before"))
            and not _is_retired(address, actions)
        ):
            rejected.append(f"{address} ({'/'.join(actions)})")
    return sorted(rejected)


def main() -> int:
    try:
        rejected = rejected_deletions(json.load(sys.stdin))
    except (json.JSONDecodeError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    if not rejected:
        return 0
    print("Terraform resources cannot be deleted or replaced:", file=sys.stderr)
    for resource in rejected:
        print(resource, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
