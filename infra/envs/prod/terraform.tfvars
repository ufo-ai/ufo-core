region = "us-east-1"

image_tag = "latest"

# The join door, deliberately guessable. Production signup is open to anyone who tries the obvious
# segment, which is the point: this is the soft door that replaces waitlist approval ahead of the
# waitlist being removed. The key is not a secret and nothing here rests on it being one — what it
# authorizes is founding a workspace for a domain WorkOS says the member owns, and nothing else.
signup_key = "ufo"
