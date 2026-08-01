region = "us-east-1"

image_tag = "latest"

# UFO's own operator Slack workspace (metalcrafthq). The gateway refuses to mutate a channel in any
# other team, so a wrong id fails the delivery rather than reaching a customer.
slack_connect_team_id = "T0BCDQWSPU2"
slack_connect_enabled = true
