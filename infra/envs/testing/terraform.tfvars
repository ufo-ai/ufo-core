region = "us-east-1"

# Set a pushed ufo-control image tag (git short SHA) here, or pass -var image_tag on apply
# (deploy.yml does). "latest" is only a placeholder for a first substrate-only apply.
image_tag = "latest"

# UFO's own operator Slack workspace (metalcrafthq). The gateway refuses to mutate a channel in any
# other team, so a wrong id fails the delivery rather than reaching a customer.
slack_connect_team_id = "T0BCDQWSPU2"
slack_connect_enabled = true
