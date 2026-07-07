region            = "us-east-1"
letsencrypt_email = "alex@metalcraft.ai"

# Stand prod up substrate-first (enable_app = false). Promote a testing-proven image SHA
# here and set enable_app = true only after testing.flyingobject.ai is verified.
image_tag  = "latest"
enable_app = false
