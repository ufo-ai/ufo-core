module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.31"

  cluster_name    = "${local.name}-cluster"
  cluster_version = var.kubernetes_version

  cluster_endpoint_public_access = true

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

  kms_key_administrators = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  cluster_addons = {
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
    metrics-server         = {}
  }

  eks_managed_node_group_defaults = merge(
    {
      ami_type                       = "AL2023_x86_64_STANDARD"
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
