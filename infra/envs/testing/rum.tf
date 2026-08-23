# The portal's Real User Monitoring application, including the session recording. A recording is
# made in the member's browser and reaches Datadog from there, so the page needs an application to
# record into and a token to write with — both are read from this resource and handed to the pods,
# never built into the bundle. `env:testing` on every session is what separates these recordings
# from the fleet's other telemetry, which the collector already stamps with the same value.
resource "datadog_rum_application" "portal" {
  name = "ufo portal (testing)"
  type = "browser"
}
