# The proxy's configuration, held in its own region. It carries the database URL, so it is a secret
# and travels through the secrets channel rather than the task definition, which anyone who can
# read a task can read. The proxy reads nine settings and no more: the key env var names, the pack,
# its port and public base, the blob store its artifact rules derive from, and its drain window.
resource "aws_secretsmanager_secret" "config" {
  name = "${var.name}/proxy-config"
  tags = var.tags
}

resource "aws_secretsmanager_secret_version" "config" {
  secret_id     = aws_secretsmanager_secret.config.id
  secret_string = var.config_toml
}
