module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.31"

  cluster_name    = "${local.name}-cluster"
  cluster_version = var.kubernetes_version

  # Public endpoint so terraform (and operators) can reach the API; restrict via
  # cluster_endpoint_public_access_cidrs in the env if you want to lock it down.
  cluster_endpoint_public_access = true

  # Cluster admin must NOT depend on who runs `terraform apply` — the creator-perms shortcut binds an
  # access entry to the caller's identity, so root (local) and the CI deploy role would churn/collide
  # over it. Instead disable it and enumerate admins explicitly (applier-independent). Every principal
  # that needs kubectl is named in cluster_admin_principal_arns: an access entry matches one exact
  # principal, so a human's Identity Center role is listed there in its own right.
  enable_cluster_creator_admin_permissions = false

  access_entries = {
    for arn in var.cluster_admin_principal_arns : arn => {
      principal_arn = arn
      policy_associations = {
        admin = {
          policy_arn   = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
          access_scope = { type = "cluster" }
        }
      }
    }
  }

  # Pin the secret-encryption KMS key admin to the account root (valid in a key policy, and it
  # delegates to IAM) so the key policy doesn't churn with the applier identity either.
  kms_key_administrators = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  cluster_addons = {
    # CoreDNS replicas must land on distinct nodes: the addon default is only a soft
    # anti-affinity, and co-located replicas turn one node's loss into a cluster-wide
    # external-DNS outage while internal DNS keeps answering.
    coredns = {
      configuration_values = jsonencode({
        affinity = {
          podAntiAffinity = {
            requiredDuringSchedulingIgnoredDuringExecution = [{
              labelSelector = { matchLabels = { "k8s-app" = "kube-dns" } }
              topologyKey   = "kubernetes.io/hostname"
            }]
          }
        }
      })
    }
    kube-proxy             = {}
    eks-pod-identity-agent = {}
    vpc-cni                = { before_compute = true }
    # Without this nothing serves the metrics API: `kubectl top` reports no node and no pod, and a
    # sizing decision has only a load average read out of a container.
    metrics-server = {}
  }

  eks_managed_node_group_defaults = merge(
    {
      ami_type = "AL2023_x86_64_STANDARD"
      # Without this, node AMIs only refresh on cluster-version bumps — kernel and NIC-driver
      # fixes go stale for months and known panics keep hard-resetting nodes.
      use_latest_ami_release_version = true
    },
    var.node_disk_size == null ? {} : {
      block_device_mappings = {
        root = {
          device_name = "/dev/xvda"
          ebs = {
            delete_on_termination = true
            encrypted             = true
            volume_size           = var.node_disk_size
            volume_type           = "gp3"
          }
        }
      }
    },
  )

  eks_managed_node_groups = {
    default = {
      instance_types = var.node_instance_types
      min_size       = var.node_min_size
      max_size       = var.node_max_size
      desired_size   = var.node_desired_size

      labels = { "flyingobject.ai/pool" = "default" }
    }
  }

  tags = local.tags
}
