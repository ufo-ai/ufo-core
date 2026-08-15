data "aws_region" "current" {}

# A replica keeps its primary's name and suffix, so its ARN is the primary's with the region
# swapped. Reading it back with a data source would fail instead: the replicas are created by this
# same configuration, and a data source is resolved at plan time, before they exist.
locals {
  replicated_secret_arns = {
    for name, arn in {
      postgres = var.postgres_secret_arn
      platform = var.platform_secret_arn
      api_keys = var.api_keys_secret_arn
    } : name => replace(arn, data.aws_region.east.name, data.aws_region.current.name)
  }
}

resource "aws_security_group" "tasks" {
  name        = "${var.name}-proxy-west-tasks"
  description = "Sandbox egress proxy tasks"
  vpc_id      = aws_vpc.this.id
  tags        = merge(var.tags, { Name = "${var.name}-proxy-west-tasks" })
}

# The load balancer terminates TLS and connects from inside this VPC, so the tasks admit the VPC
# rather than the internet: a sandbox reaches the proxy through the listener, never the port.
resource "aws_security_group_rule" "tasks_from_lb" {
  type              = "ingress"
  from_port         = 8888
  to_port           = 8888
  protocol          = "tcp"
  security_group_id = aws_security_group.tasks.id
  cidr_blocks       = [var.vpc_cidr]
  description       = "Proxy port from the load balancer"
}

resource "aws_security_group_rule" "tasks_egress" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  security_group_id = aws_security_group.tasks.id
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "Outbound egress, which is what the proxy exists to make"
}

resource "aws_lb" "this" {
  name               = "${var.name}-proxy-west"
  load_balancer_type = "network"
  internal           = false
  subnets            = aws_subnet.public[*].id

  # A sandbox resolves one of the load balancer's addresses at random. Without this, an address in
  # a zone whose task is unhealthy answers nothing.
  enable_cross_zone_load_balancing = true
  tags                             = var.tags
}

resource "aws_lb_target_group" "this" {
  name        = "${var.name}-proxy-west"
  port        = 8888
  protocol    = "TCP"
  target_type = "ip"
  vpc_id      = aws_vpc.this.id
  tags        = var.tags

  health_check {
    protocol            = "TCP"
    healthy_threshold   = 2
    unhealthy_threshold = 2
    interval            = 10
  }
}

resource "aws_lb_listener" "this" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "TLS"
  certificate_arn   = var.certificate_arn
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  tags              = var.tags

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this.arn
  }
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ufo/${var.name}/proxy-west"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# The execution role pulls the image, writes logs, and reads exactly the four secrets the task
# names — nothing else in the account's Secrets Manager.
resource "aws_iam_role" "execution" {
  name               = "${var.name}-proxy-west-execution"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "secrets" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.config.arn,
      local.replicated_secret_arns.postgres,
      local.replicated_secret_arns.platform,
      local.replicated_secret_arns.api_keys,
    ]
  }
}

resource "aws_iam_role_policy" "secrets" {
  name   = "secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.secrets.json
}

resource "aws_iam_role" "task" {
  name               = "${var.name}-proxy-west-task"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

resource "aws_ecs_cluster" "this" {
  name = "${var.name}-proxy-west"
  tags = var.tags
}

# `ufoctl` is the image's entrypoint and the config arrives as a value, not a file, because ECS
# mounts no volumes for secrets. The task writes it out and execs the same command the cluster's
# deployment runs, so the two differ in delivery and not in what runs.
resource "aws_ecs_task_definition" "this" {
  family                   = "${var.name}-proxy-west"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  skip_destroy             = true
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  tags                     = var.tags

  container_definitions = jsonencode([{
    name       = "proxy"
    image      = var.image
    essential  = true
    entryPoint = ["/bin/sh", "-c"]
    command    = ["printf '%s' \"$UFO_CONFIG_TOML\" > /tmp/ufo.toml && exec ufoctl proxy"]

    portMappings = [{ containerPort = 8888, protocol = "tcp" }]

    environment = [
      { name = "UFO_CONFIG", value = "/tmp/ufo.toml" },
      { name = "UFO_OTLP_ENDPOINT", value = var.otlp_endpoint },
    ]

    secrets = [
      { name = "UFO_CONFIG_TOML", valueFrom = aws_secretsmanager_secret.config.arn },
      { name = "UFO_OWNER_DSN", valueFrom = "${local.replicated_secret_arns.postgres}:postgres-admin-dsn::" },
      { name = "UFO_TOKEN_SECRET", valueFrom = "${local.replicated_secret_arns.platform}:ufo-token-secret::" },
      { name = "UFO_EGRESS_CA_CERT", valueFrom = "${local.replicated_secret_arns.platform}:egress-ca-cert::" },
      { name = "UFO_EGRESS_CA_KEY", valueFrom = "${local.replicated_secret_arns.platform}:egress-ca-key::" },
      { name = "UFO_CREDENTIAL_KEY", valueFrom = "${local.replicated_secret_arns.platform}:serve-credential-key::" },
      { name = "ANTHROPIC_API_KEY", valueFrom = "${local.replicated_secret_arns.api_keys}:anthropic-api-key::" },
      { name = "OPENAI_API_KEY", valueFrom = "${local.replicated_secret_arns.api_keys}:openai-api-key::" },
      { name = "COMPOSIO_API_KEY", valueFrom = "${local.replicated_secret_arns.api_keys}:composio-api-key::" },
      { name = "GITHUB_APP_ID", valueFrom = "${local.replicated_secret_arns.api_keys}:github-app-id::" },
      { name = "GITHUB_APP_CLIENT_ID", valueFrom = "${local.replicated_secret_arns.api_keys}:github-app-client-id::" },
      { name = "GITHUB_APP_CLIENT_SECRET", valueFrom = "${local.replicated_secret_arns.api_keys}:github-app-client-secret::" },
      { name = "GITHUB_APP_PRIVATE_KEY", valueFrom = "${local.replicated_secret_arns.api_keys}:github-app-private-key::" },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.this.name
        awslogs-region        = data.aws_region.current.name
        awslogs-stream-prefix = "proxy"
      }
    }
  }])
}

resource "aws_ecs_service" "this" {
  name            = "${var.name}-proxy-west"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.this.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  # The same apply points sandboxes at this service, so an apply that reports success while no task
  # can start would move traffic to nothing.
  wait_for_steady_state = true
  tags                  = var.tags

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.this.arn
    container_name   = "proxy"
    container_port   = 8888
  }

  depends_on = [aws_lb_listener.this]
}
