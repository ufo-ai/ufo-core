region = "us-east-1"

image_tag = "latest"

# UFO's own operator Slack workspace (metalcrafthq). The gateway refuses to mutate a channel in any
# other team, so a wrong id fails the delivery rather than reaching a customer.
slack_connect_team_id = "T0BCDQWSPU2"
slack_connect_enabled = true

# The join door. Testing runs the same path production does, under a value nobody types by accident,
# so a member of this environment is somebody who was given the link rather than anybody at all.
signup_key = "61fcacb5-c6a2-4f2e-bf03-22fdbb6ef25d"
