import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
GUARD = ROOT / ".github" / "scripts" / "terraform_plan_guard.py"
SWEPT_KEY = "enable-swept-away"
PERSISTENT_DELETIONS = (
    ("module.platform.module.rds.module.db_instance.aws_db_instance.this[0]", "aws_db_instance"),
    ("module.platform.aws_ecr_repository.this", "aws_ecr_repository"),
    (
        "module.platform.aws_elasticache_replication_group.redis",
        "aws_elasticache_replication_group",
    ),
    ("module.platform.module.eks.aws_eks_cluster.this[0]", "aws_eks_cluster"),
    (
        "module.platform.module.eks.aws_cloudwatch_log_group.this[0]",
        "aws_cloudwatch_log_group",
    ),
    ("module.platform.aws_iam_policy.app_s3", "aws_iam_policy"),
    (
        "module.platform.module.eks.aws_iam_openid_connect_provider.oidc[0]",
        "aws_iam_openid_connect_provider",
    ),
    ("aws_iam_role.datadog", "aws_iam_role"),
    ("aws_iam_role_policy.datadog_rds_metrics", "aws_iam_role_policy"),
    ("module.platform.module.eks.module.kms.aws_kms_key.this[0]", "aws_kms_key"),
    ("module.platform.aws_s3_bucket.blob", "aws_s3_bucket"),
    ("module.platform.aws_s3_bucket_ownership_controls.blob", "aws_s3_bucket_ownership_controls"),
    ("module.platform.aws_s3_bucket_public_access_block.blob", "aws_s3_bucket_public_access_block"),
    ("module.platform.aws_s3_bucket_versioning.blob", "aws_s3_bucket_versioning"),
    ("module.platform.aws_secretsmanager_secret.postgres", "aws_secretsmanager_secret"),
    ("module.platform.aws_sesv2_email_identity.onboard", "aws_sesv2_email_identity"),
    ("cloudflare_dns_record.ses_dkim", "cloudflare_dns_record"),
    ("datadog_integration_aws_account.ufo", "datadog_integration_aws_account"),
    ("datadog_integration_aws_external_id.ufo", "datadog_integration_aws_external_id"),
    ("module.platform.random_id.serve_credential_key", "random_id"),
    ("module.platform.random_password.rds", "random_password"),
    ("module.platform.tls_private_key.egress_ca", "tls_private_key"),
    ("module.platform.module.vpc.aws_nat_gateway.this[0]", "aws_nat_gateway"),
    ("module.platform.module.vpc.aws_subnet.private[0]", "aws_subnet"),
    ("module.platform.module.vpc.aws_vpc.this[0]", "aws_vpc"),
)
REGENERABLE_TYPE_DELETIONS = (
    ("module.platform.aws_acm_certificate.sandbox_proxy_public", "aws_acm_certificate"),
    (
        "module.platform.aws_acm_certificate_validation.sandbox_proxy_public",
        "aws_acm_certificate_validation",
    ),
    ("module.platform.aws_ecr_lifecycle_policy.this", "aws_ecr_lifecycle_policy"),
    ("module.platform.aws_elasticache_subnet_group.redis", "aws_elasticache_subnet_group"),
    (
        "module.platform.aws_s3_bucket_server_side_encryption_configuration.blob",
        "aws_s3_bucket_server_side_encryption_configuration",
    ),
    (
        "module.platform.aws_secretsmanager_secret_version.postgres[0]",
        "aws_secretsmanager_secret_version",
    ),
    ("module.platform.aws_security_group.rds", "aws_security_group"),
    ("module.platform.aws_security_group_rule.rds_from_nodes", "aws_security_group_rule"),
    ("cloudflare_ruleset.flyingobject_redirect", "cloudflare_ruleset"),
    ("cloudflare_ruleset.shipped_app_cache", "cloudflare_ruleset"),
    ("module.prod.cloudflare_workers_route.edge", "cloudflare_workers_route"),
    ("module.prod.cloudflare_workers_script.edge", "cloudflare_workers_script"),
    ("cloudflare_zone_setting.always_use_https", "cloudflare_zone_setting"),
    ("cloudflare_zone_setting.minimum_tls_version", "cloudflare_zone_setting"),
    ("datadog_dashboard.database", "datadog_dashboard"),
    ("datadog_metric_metadata.turn_ms", "datadog_metric_metadata"),
    ("datadog_metric_tag_configuration.turn_ms", "datadog_metric_tag_configuration"),
    ("datadog_monitor.telemetry_silent", "datadog_monitor"),
    ("helm_release.ingress_nginx", "helm_release"),
    ("kubectl_manifest.ufo", "kubectl_manifest"),
    ("kubernetes_namespace_v1.ufo_system", "kubernetes_namespace_v1"),
    ("module.platform.kubernetes_secret.cloudflare_api_token", "kubernetes_secret"),
    ("kubernetes_secret_v1.ufo_serve", "kubernetes_secret_v1"),
    ('terraform_data.metric_seed["ufo.turn_ms"]', "terraform_data"),
    ("module.platform.tls_self_signed_cert.egress_ca", "tls_self_signed_cert"),
)
REGENERABLE_MODULE_DELETIONS = (
    (
        'module.platform.cloudflare_dns_record.sandbox_proxy_validation["sandbox-proxy.testing.flyingobject.ai"]',
        "cloudflare_dns_record",
    ),
    (
        'module.platform.module.eks.aws_ec2_tag.cluster_primary_security_group["Environment"]',
        "aws_ec2_tag",
    ),
    ('module.platform.module.eks.aws_eks_addon.this["coredns"]', "aws_eks_addon"),
    (
        'module.platform.module.eks.aws_eks_addon.before_compute["vpc-cni"]',
        "aws_eks_addon",
    ),
    (
        'module.platform.module.eks.aws_eks_access_entry.this["arn:aws:iam::899147036157:root"]',
        "aws_eks_access_entry",
    ),
    (
        'module.platform.module.eks.aws_eks_access_policy_association.this["arn:aws:iam::899147036157:root_admin"]',
        "aws_eks_access_policy_association",
    ),
    (
        'module.platform.module.eks.module.eks_managed_node_group["default"].aws_eks_node_group.this[0]',
        "aws_eks_node_group",
    ),
    (
        'module.platform.module.eks.aws_iam_role_policy_attachment.this["AmazonEKSClusterPolicy"]',
        "aws_iam_role_policy_attachment",
    ),
    (
        'module.platform.module.eks.module.kms.aws_kms_alias.this["cluster"]',
        "aws_kms_alias",
    ),
    (
        'module.platform.module.eks.module.eks_managed_node_group["default"].aws_launch_template.this[0]',
        "aws_launch_template",
    ),
    (
        'module.platform.module.eks.module.eks_managed_node_group["default"].module.user_data.null_resource.validate_cluster_service_cidr',
        "null_resource",
    ),
    (
        "module.platform.module.eks.time_sleep.this[0]",
        "time_sleep",
    ),
    (
        'module.platform.module.irsa_app_s3.aws_iam_role_policy_attachment.this["s3"]',
        "aws_iam_role_policy_attachment",
    ),
    (
        "module.platform.module.irsa_external_secrets.aws_iam_role_policy_attachment.external_secrets[0]",
        "aws_iam_role_policy_attachment",
    ),
    (
        'module.platform.module.irsa_gateway_ses.aws_iam_role_policy_attachment.this["ses"]',
        "aws_iam_role_policy_attachment",
    ),
    (
        "module.platform.module.irsa_lb_controller.aws_iam_role_policy_attachment.load_balancer_controller[0]",
        "aws_iam_role_policy_attachment",
    ),
    (
        "module.platform.module.rds.module.db_parameter_group.aws_db_parameter_group.this[0]",
        "aws_db_parameter_group",
    ),
    (
        "module.platform.module.rds.module.db_subnet_group.aws_db_subnet_group.this[0]",
        "aws_db_subnet_group",
    ),
    ("module.platform.module.vpc.aws_default_network_acl.this[0]", "aws_default_network_acl"),
    (
        "module.platform.module.vpc.aws_default_route_table.default[0]",
        "aws_default_route_table",
    ),
    (
        "module.platform.module.vpc.aws_default_security_group.this[0]",
        "aws_default_security_group",
    ),
    ("module.platform.module.vpc.aws_eip.nat[0]", "aws_eip"),
    ("module.platform.module.vpc.aws_internet_gateway.this[0]", "aws_internet_gateway"),
    ("module.platform.module.vpc.aws_route.private_nat_gateway[0]", "aws_route"),
    ("module.platform.module.vpc.aws_route_table.private[0]", "aws_route_table"),
    (
        "module.platform.module.vpc.aws_route_table_association.private[0]",
        "aws_route_table_association",
    ),
)


def _guard(tombstones: tuple[str, ...] | None = (SWEPT_KEY,)):
    """The guard, with a tombstone list of the case's own. The repo's list is swept — an entry is
    dropped once no environment holds the key — so a case naming one of its entries goes red on the
    day it is dropped. `None` keeps what the module read, which is what proves that wiring."""
    spec = importlib.util.spec_from_file_location("terraform_plan_guard", GUARD)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if tombstones is not None:
        module.FLAG_TOMBSTONES = frozenset(tombstones)
    return module


def _change(
    address: str, resource_type: str, actions: list[object], before: object | None = None
) -> dict[str, object]:
    change: dict[str, object] = {"actions": actions}
    if before is not None:
        change["before"] = before
    return {
        "address": address,
        "type": resource_type,
        "change": change,
    }


def _run(plan: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(plan),
        capture_output=True,
        text=True,
    )


def _check_rejects_persistent_deletions() -> None:
    guard = _guard()
    for address, resource_type in PERSISTENT_DELETIONS:
        for actions in (["delete"], ["delete", "create"], ["create", "delete"]):
            plan = {"resource_changes": [_change(address, resource_type, actions)]}
            assert guard.rejected_deletions(plan) == [f"{address} ({'/'.join(actions)})"]


def _check_allows_regenerable_deletions() -> None:
    guard = _guard()
    cases = REGENERABLE_TYPE_DELETIONS + REGENERABLE_MODULE_DELETIONS
    for address, resource_type in cases:
        for actions in (["delete"], ["delete", "create"], ["create", "delete"]):
            plan = {"resource_changes": [_change(address, resource_type, actions)]}
            assert guard.rejected_deletions(plan) == []


def _check_allows_a_tombstoned_flag_deletion() -> None:
    for address in (
        f'cloudflare_flagship_flag.testing_portal["{SWEPT_KEY}"]',
        f'cloudflare_flagship_flag.prod_portal["{SWEPT_KEY}"]',
    ):
        plan = {
            "resource_changes": [
                _change(
                    address,
                    "cloudflare_flagship_flag",
                    ["delete"],
                    before={"flag_key": SWEPT_KEY},
                )
            ]
        }
        assert _guard().rejected_deletions(plan) == []


def _check_rejects_other_flag_deletions() -> None:
    cases = (
        (
            'cloudflare_flagship_flag.testing_portal["enable-memory-tab"]',
            ["delete"],
            {"flag_key": "enable-memory-tab"},
        ),
        (
            f'cloudflare_flagship_flag.testing_portal["{SWEPT_KEY}"]',
            ["delete", "create"],
            {"flag_key": SWEPT_KEY},
        ),
        (
            'cloudflare_flagship_flag.testing_portal["enable-memory-tab"]',
            ["delete"],
            {"flag_key": SWEPT_KEY},
        ),
    )
    for address, actions, before in cases:
        plan = {
            "resource_changes": [
                _change(address, "cloudflare_flagship_flag", actions, before=before)
            ]
        }
        assert _guard().rejected_deletions(plan) == [f"{address} ({'/'.join(actions)})"]


def _check_regenerable_cases_cover_the_guard_tables() -> None:
    guard = _guard()
    assert {resource_type for _, resource_type in REGENERABLE_TYPE_DELETIONS} == (
        guard.REGENERABLE_RESOURCE_TYPES
    )

    def owner(address: str) -> str | None:
        matches = [
            prefix
            for prefix in guard.REGENERABLE_MODULE_RESOURCE_TYPES
            if address.startswith(f"{prefix}.")
        ]
        return max(matches, key=len) if matches else None

    for prefix, resource_types in guard.REGENERABLE_MODULE_RESOURCE_TYPES.items():
        assert {
            resource_type
            for address, resource_type in REGENERABLE_MODULE_DELETIONS
            if owner(address) == prefix
        } == resource_types
    assert all(owner(address) is not None for address, _ in REGENERABLE_MODULE_DELETIONS)


def _check_local_cases_name_declared_resources() -> None:
    declared = set()
    roots = (
        (ROOT / "infra" / "envs" / "testing", ""),
        (ROOT / "infra" / "envs" / "prod", ""),
        (ROOT / "infra" / "envs" / "edge", ""),
        (ROOT / "infra" / "modules" / "platform", "module.platform."),
        (ROOT / "infra" / "modules" / "edge", "module.prod."),
        (ROOT / "infra" / "modules" / "edge", "module.testing."),
    )
    for root, prefix in roots:
        for path in sorted(root.glob("*.tf")):
            declared.update(
                f"{prefix}{resource_type}.{name}"
                for resource_type, name in re.findall(
                    r'^resource "([^"]+)" "([^"]+)"', path.read_text(), re.MULTILINE
                )
            )
    cases = REGENERABLE_TYPE_DELETIONS + tuple(
        (address, resource_type)
        for address, resource_type in PERSISTENT_DELETIONS
        if not address.startswith(
            (
                "module.platform.module.eks.",
                "module.platform.module.rds.",
                "module.platform.module.vpc.",
            )
        )
    )
    assert {address.split("[", maxsplit=1)[0] for address, _ in cases} <= declared


def _check_allows_non_deletions() -> None:
    for actions in (["create"], ["update"], ["no-op"], ["read"]):
        plan = {"resource_changes": [_change("example.resource", "future_resource", actions)]}
        assert _guard().rejected_deletions(plan) == []


def _check_rejects_module_scoped_type_outside_its_module() -> None:
    for address in (
        "module.platform.module.other.time_sleep.this",
        "module.platform.module.eks_other.time_sleep.this",
    ):
        plan = {"resource_changes": [_change(address, "time_sleep", ["delete"])]}
        assert _guard().rejected_deletions(plan) == [f"{address} (delete)"]


def _check_rejects_an_unknown_resource_deletion() -> None:
    plan = {"resource_changes": [_change("future.database", "future_database", ["delete"])]}
    assert _guard().rejected_deletions(plan) == ["future.database (delete)"]


def _check_allows_a_plan_with_no_resource_changes() -> None:
    plans = ({"format_version": "1.2"}, {"format_version": "1.2", "resource_changes": []})
    assert all(_guard().rejected_deletions(plan) == [] for plan in plans)


def _check_rejects_invalid_plan_shapes() -> None:
    cases = (
        ({"resource_changes": None}, "invalid resource_changes"),
        ({"resource_changes": "none"}, "invalid resource_changes"),
        ({}, "no resource_changes list"),
        ([], "no resource_changes list"),
    )
    for plan, error in cases:
        with pytest.raises(ValueError, match=error):
            _guard().rejected_deletions(plan)


def _check_rejects_invalid_resource_shapes() -> None:
    cases = (
        ({"type": "aws_s3_bucket", "change": {"actions": ["delete"]}}, "invalid shape"),
        ({"address": "bucket", "change": {"actions": ["delete"]}}, "invalid shape"),
        ({"address": "bucket", "type": "aws_s3_bucket", "change": {}}, "invalid shape"),
        (_change("bucket", "aws_s3_bucket", [["delete"]]), "actions must be strings"),
    )
    for resource, error in cases:
        with pytest.raises(ValueError, match=error):
            _guard().rejected_deletions({"resource_changes": [resource]})


def _check_entrypoint_reports_every_rejected_address() -> None:
    plan = {
        "resource_changes": [
            _change("module.platform.random_password.rds", "random_password", ["delete", "create"]),
            _change("module.platform.aws_db_instance.this", "aws_db_instance", ["delete"]),
        ]
    }
    rejected = _run(plan)
    assert rejected.returncode == 1
    assert rejected.stdout == ""
    assert rejected.stderr == (
        "Terraform resources cannot be deleted or replaced:\n"
        "module.platform.aws_db_instance.this (delete)\n"
        "module.platform.random_password.rds (delete/create)\n"
    )


def _check_entrypoint_accepts_a_clean_plan() -> None:
    allowed = _run({"format_version": "1.2"})
    assert allowed.returncode == 0
    assert allowed.stdout == ""
    assert allowed.stderr == ""


def _check_entrypoint_reports_malformed_json_without_a_traceback() -> None:
    rejected = subprocess.run(
        [sys.executable, str(GUARD)],
        input="not-json",
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 1
    assert rejected.stdout == ""
    assert rejected.stderr
    assert "Traceback" not in rejected.stderr


def _check_entrypoint_reports_invalid_plans_without_a_traceback() -> None:
    cases = (
        (
            {"resource_changes": "none"},
            "Terraform plan JSON has an invalid resource_changes value",
        ),
        ({}, "Terraform plan JSON has no resource_changes list"),
        ({"resource_changes": [{}]}, "Terraform resource change has an invalid shape"),
        (
            {"resource_changes": [_change("bucket", "aws_s3_bucket", [["delete"]])]},
            "Terraform resource actions must be strings",
        ),
    )
    for plan, error in cases:
        rejected = _run(plan)
        assert rejected.returncode == 1
        assert rejected.stdout == ""
        assert rejected.stderr == f"{error}\n"


def _check_the_tombstone_list_is_read_from_the_repo() -> None:
    """The flag cases swap the list out, so this is what holds the guard to the real one."""
    held = json.loads((ROOT / "infra" / "flag_tombstones.json").read_text())
    assert _guard(tombstones=None).FLAG_TOMBSTONES == frozenset(held)


def _check_allows_a_retired_resource_deletion() -> None:
    """A retirement is what lets the deploy destroy a database or a queue, which the guard refuses
    to every other address. The record is read from the repo, so a case reads it too."""
    retired = json.loads((ROOT / "infra" / "retired_resources.json").read_text())
    assert retired, "the guard's retirement branch cannot be proved with an empty record"
    for address in retired:
        plan = {"resource_changes": [_change(address, address.split(".")[-2], ["delete"])]}
        assert _guard().rejected_deletions(plan) == []


def _check_rejects_a_retired_address_that_is_replaced_rather_than_destroyed() -> None:
    """A retirement destroys once. `delete/create` is a replace, which loses the rows and keeps the
    resource — the case the guard exists for."""
    address = json.loads((ROOT / "infra" / "retired_resources.json").read_text())[0]
    plan = {"resource_changes": [_change(address, address.split(".")[-2], ["delete", "create"])]}
    assert _guard().rejected_deletions(plan) == [f"{address} (delete/create)"]


def _check_allows_the_metric_seed_to_be_dropped_or_replaced() -> None:
    """The seed is declared `for_each` over the histogram names, so renaming or removing one plans
    a delete of its instance, and a failed seed provisioner taints its instance so every later plan
    proposes the replace. Refusing either stops the deploy at this guard before the apply, on the
    pull request and on main, so the permission is read against the shape the root declares."""
    seed = re.search(
        r'resource "(\w+)" "metric_seed"',
        (ROOT / "infra" / "envs" / "testing" / "metrics.tf").read_text(),
    )
    assert seed
    resource_type = seed.group(1)
    address = f'{resource_type}.metric_seed["ufo.turn_ms"]'
    for actions in (["delete"], ["delete", "create"], ["create", "delete"]):
        plan = {"resource_changes": [_change(address, resource_type, actions)]}
        assert _guard().rejected_deletions(plan) == []


def _check_an_unretired_address_of_a_retired_type_is_still_refused() -> None:
    """The record names addresses, never types: retiring one database does not make the next one
    deletable."""
    plan = {
        "resource_changes": [
            _change(
                "module.prod.cloudflare_d1_database.other", "cloudflare_d1_database", ["delete"]
            )
        ]
    }
    assert _guard().rejected_deletions(plan) == [
        "module.prod.cloudflare_d1_database.other (delete)"
    ]


def test_terraform_plan_guard_contract() -> None:
    checks = tuple(value for name, value in globals().items() if name.startswith("_check_"))
    assert len(checks) == 21
    for check in checks:
        check()
