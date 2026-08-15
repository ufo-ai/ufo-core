data "aws_availability_zones" "this" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.this.names, 0, var.az_count)
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = merge(var.tags, { Name = "${var.name}-proxy-west" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${var.name}-proxy-west" })
}

# Public subnets, and the tasks take public addresses: the proxy's own egress is the whole point of
# the service, so a NAT gateway per AZ would add a hop and a bill to reach the internet it exists to
# reach. Reachability is the security group's job, not the subnet's.
resource "aws_subnet" "public" {
  count                   = var.az_count
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true
  tags                    = merge(var.tags, { Name = "${var.name}-proxy-west-${local.azs[count.index]}" })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${var.name}-proxy-west" })
}

resource "aws_route" "default" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  count          = var.az_count
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Postgres is the one thing the proxy needs from the platform region: it resolves rules and reads
# each workspace's credentials per turn. Peering carries only that; every other call the proxy makes
# leaves through this region's own gateway.
resource "aws_vpc_peering_connection" "east" {
  vpc_id      = aws_vpc.this.id
  peer_vpc_id = var.east_vpc_id
  peer_region = data.aws_region.east.name
  tags        = merge(var.tags, { Name = "${var.name}-proxy-west-to-platform" })
}

data "aws_region" "east" {
  provider = aws.east
}

resource "aws_vpc_peering_connection_accepter" "east" {
  provider                  = aws.east
  vpc_peering_connection_id = aws_vpc_peering_connection.east.id
  auto_accept               = true
  tags                      = merge(var.tags, { Name = "${var.name}-proxy-west-to-platform" })
}

resource "aws_route" "to_east" {
  route_table_id            = aws_route_table.public.id
  destination_cidr_block    = var.east_vpc_cidr
  vpc_peering_connection_id = aws_vpc_peering_connection.east.id
}

resource "aws_route" "from_east" {
  provider                  = aws.east
  count                     = length(var.east_route_table_ids)
  route_table_id            = var.east_route_table_ids[count.index]
  destination_cidr_block    = var.vpc_cidr
  vpc_peering_connection_id = aws_vpc_peering_connection_accepter.east.vpc_peering_connection_id
}

# A security group reference cannot cross a region, so Postgres admits this VPC by CIDR. The range is
# private and peered to exactly one VPC, so the CIDR names the same set the group reference would.
resource "aws_security_group_rule" "rds_from_proxy_west" {
  provider          = aws.east
  type              = "ingress"
  from_port         = 5432
  to_port           = 5432
  protocol          = "tcp"
  security_group_id = var.east_rds_security_group_id
  cidr_blocks       = [var.vpc_cidr]
  description       = "Postgres from the colocated sandbox egress proxy"
}
