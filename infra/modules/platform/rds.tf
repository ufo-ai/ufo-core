resource "aws_security_group" "rds" {
  name        = "${local.name}-rds"
  description = "Postgres access from the EKS node group"
  vpc_id      = module.vpc.vpc_id
  tags        = local.tags
}

resource "aws_security_group_rule" "rds_from_nodes" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.rds.id
  source_security_group_id = module.eks.node_security_group_id
  description              = "Postgres from EKS nodes"
}

module "rds" {
  source  = "terraform-aws-modules/rds/aws"
  version = "~> 6.10"

  identifier = "${local.name}-postgres"

  engine               = "postgres"
  engine_version       = var.postgres_version
  family               = "postgres${var.postgres_version}"
  major_engine_version = var.postgres_version
  instance_class       = var.rds_instance_class

  allocated_storage     = var.rds_allocated_storage
  max_allocated_storage = var.rds_max_allocated_storage
  storage_encrypted     = true

  db_name  = var.app_database_name
  username = "metalcraft"
  port     = 5432

  # We compose the connection URLs into our own Secrets Manager entry (see secrets.tf),
  # so disable the module's AWS-managed master password.
  manage_master_user_password = false
  password                    = random_password.rds.result

  multi_az               = var.rds_multi_az
  subnet_ids             = module.vpc.private_subnets
  create_db_subnet_group = true
  vpc_security_group_ids = [aws_security_group.rds.id]

  # pgvector is available on RDS PG16 but unused today; enable here if the brain
  # module moves vectors off TurboPuffer into Postgres.
  # On by default in every env: the DB holds all tenant + turn state, so the deploy pipeline must
  # never be able to drop it. Recreating an env means flipping this off by hand first.
  deletion_protection     = var.rds_deletion_protection
  backup_retention_period = 7
  skip_final_snapshot     = !var.rds_multi_az

  tags = local.tags
}
