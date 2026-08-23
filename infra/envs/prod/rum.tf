# The portal's Real User Monitoring application, including the session recording. A recording is
# made in the member's browser and reaches Datadog from there, so the page needs an application to
# record into and a token to write with — both are read from this resource and handed to the pods,
# never built into the bundle. `env:prod` on every session is what separates these recordings from
# testing's, which record into an application of their own.
resource "datadog_rum_application" "portal" {
  name = "ufo portal"
  type = "browser"
}
