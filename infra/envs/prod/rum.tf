# A recording is made in the member's browser, so the page needs an application to record into and a
# token to write with — both read from this resource and handed to the pods, never built into the bundle.
resource "datadog_rum_application" "portal" {
  name = "ufo portal"
  type = "browser"
}
