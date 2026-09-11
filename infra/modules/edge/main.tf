
terraform {
  required_providers {
    cloudflare = { source = "cloudflare/cloudflare", version = "~> 5.12" }
  }
}

locals {
  landing_html              = file("${path.module}/landing.html")
  inter_font                = filebase64("${path.module}/assets/fonts/Inter-VariableFont_wght.woff2")
  legal_css                 = file("${path.module}/legal.css")
  legal_shell               = file("${path.module}/legal.html")
  roboto_mono_font          = filebase64("${path.module}/assets/fonts/RobotoMono-VariableFont_wght.ttf")
  privacy_description       = "What ufo.ai collects when you sign in and use the service, how that information is used, and how long it is kept."
  slack_description         = "How to install and use ufo in Slack, what the app does, and where to get support."
  subprocessors_description = "The service providers that process information for ufo.ai and the work each provider performs."
  support_description       = "How to get support for ufo and the ufo Slack app, and how to make a privacy request."
  terms_description         = "The terms that govern your use of ufo.ai: accounts, acceptable use, intellectual property, and liability."
  privacy_html = replace(
    replace(
      replace(
        replace(local.legal_shell, "__TITLE__", "Privacy Policy"),
        "__DESCRIPTION__", local.privacy_description,
      ),
      "__CANONICAL__", "https://ufo.ai/privacy",
    ),
    "__BODY__", file("${path.module}/privacy.html"),
  )
  slack_html = replace(
    replace(
      replace(
        replace(local.legal_shell, "__TITLE__", "ufo for Slack"),
        "__DESCRIPTION__", local.slack_description,
      ),
      "__CANONICAL__", "https://ufo.ai/slack",
    ),
    "__BODY__", file("${path.module}/slack.html"),
  )
  subprocessors_html = replace(
    replace(
      replace(
        replace(local.legal_shell, "__TITLE__", "Subprocessors"),
        "__DESCRIPTION__", local.subprocessors_description,
      ),
      "__CANONICAL__", "https://ufo.ai/subprocessors",
    ),
    "__BODY__", file("${path.module}/subprocessors.html"),
  )
  support_html = replace(
    replace(
      replace(
        replace(local.legal_shell, "__TITLE__", "Support"),
        "__DESCRIPTION__", local.support_description,
      ),
      "__CANONICAL__", "https://ufo.ai/support",
    ),
    "__BODY__", file("${path.module}/support.html"),
  )
  terms_html = replace(
    replace(
      replace(
        replace(local.legal_shell, "__TITLE__", "Terms of Service"),
        "__DESCRIPTION__", local.terms_description,
      ),
      "__CANONICAL__", "https://ufo.ai/terms",
    ),
    "__BODY__", file("${path.module}/terms.html"),
  )
}

resource "cloudflare_workers_script" "edge" {
  account_id  = var.account_id
  script_name = var.name
  content = replace(
    replace(
      replace(
        replace(
          replace(
            replace(
              replace(
                replace(
                  replace(
                    replace(
                      replace(
                        file("${path.module}/worker.js"),
                        "\"__LANDING_HTML__\"",
                        jsonencode(local.landing_html),
                      ),
                      "\"__LEGAL_CSS__\"",
                      jsonencode(local.legal_css),
                    ),
                    "\"__INTER_FONT__\"",
                    jsonencode(local.inter_font),
                  ),
                  "\"__ROBOTO_MONO_FONT__\"",
                  jsonencode(local.roboto_mono_font),
                ),
                "\"__SLACK_HTML__\"",
                jsonencode(local.slack_html),
              ),
              "\"__SUPPORT_HTML__\"",
              jsonencode(local.support_html),
            ),
            "\"__FAVICON_SVG__\"",
            jsonencode(var.favicon_svg),
          ),
          "\"__FAVICON_DARK_SVG__\"",
          jsonencode(var.favicon_dark_svg),
        ),
        "\"__PRIVACY_HTML__\"",
        jsonencode(local.privacy_html),
      ),
      "\"__TERMS_HTML__\"",
      jsonencode(local.terms_html),
    ),
    "\"__SUBPROCESSORS_HTML__\"",
    jsonencode(local.subprocessors_html),
  )

  main_module = "worker.js"

  bindings = [
    {
      name = "ORIGIN_BASE"
      type = "plain_text"
      text = var.origin_base
    },
  ]
}

resource "cloudflare_workers_route" "edge" {
  zone_id = var.zone_id
  pattern = "${var.hostname}/*"
  script  = cloudflare_workers_script.edge.script_name
}

resource "cloudflare_workers_route" "site_cards" {
  zone_id = var.zone_id
  pattern = "app.${var.hostname}/surface/sites/share/site/*"
  script  = cloudflare_workers_script.edge.script_name
}

resource "cloudflare_workers_route" "artifact_bytes" {
  zone_id = var.zone_id
  pattern = "app.${var.hostname}/artifacts/*"
  script  = cloudflare_workers_script.edge.script_name
}
