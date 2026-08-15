# Ordered behind the service, not just the load balancer it sits on: the load balancer exists long
# before a task registers healthy, and the west branch owes strictly more work after it — ACM
# validation, a cross-region image pull, and `verify_db_reachable` before the port binds. A caller
# that resolves this name is therefore ordered after something answers on it, and an apply whose
# service never stabilises never reaches the caller at all.
output "load_balancer_dns_name" {
  value       = aws_lb.this.dns_name
  description = "The proxy's load balancer, once its service is steady."
  depends_on  = [aws_ecs_service.this]
}
