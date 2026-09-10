#!/usr/bin/env bash

# Only what infra/envs/edge already applied on a push to main. The configuration set belongs to the
# production apply this runs before, so checking it here would block the roll that creates it.

set -euo pipefail

IDENTITY=$(aws sesv2 get-email-identity --email-identity ufo.ai \
  --query '[VerifiedForSendingStatus, DkimAttributes.Status, MailFromAttributes.MailFromDomain, MailFromAttributes.MailFromDomainStatus]' \
  --output text)
read -r VERIFIED DKIM MAIL_FROM MAIL_FROM_STATUS <<< "$IDENTITY"
printf 'ufo.ai verified=%s dkim=%s mail_from=%s/%s\n' "$VERIFIED" "$DKIM" "$MAIL_FROM" "$MAIL_FROM_STATUS"
test "$VERIFIED" = True
test "$DKIM" = SUCCESS
test "$MAIL_FROM" = bounce.ufo.ai
test "$MAIL_FROM_STATUS" = SUCCESS

TOPIC=$(aws sesv2 get-contact-list --contact-list-name ufo-users \
  --query 'length(Topics[?TopicName == `founder-updates`])' --output text)
test "$TOPIC" = 1
