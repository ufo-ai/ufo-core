//! Signup-email verification through WorkOS, under a claim time-to-live.
//!
//! The time-to-live is the Magic Auth code's own ten minutes. WorkOS answers a code it will not
//! redeem the same way whether the digits are wrong or the code has died, so the claim's window is
//! what tells the member which happened: inside it a refused code is one to retype, and at its edge
//! the claim ends and says so.

use chrono::{DateTime, Duration, Utc};
use uuid::Uuid;

use crate::email::{EmailError, SignupEmailPolicy};
use crate::store::{OnboardClaim, OnboardStore, StoreError};
use crate::workos::{VerificationError, Verifier};

pub const CLAIM_TTL_MINUTES: i64 = 10;
pub const VERIFICATION_CHANGED: &str = "Another verification attempt changed this session.";
pub const CODE_EXPIRED: &str = "The verification code expired. Start onboarding again.";
pub const CODE_INCORRECT: &str = "The verification code is incorrect.";

/// The onboarding flow renders the message.
#[derive(Debug, Clone, thiserror::Error)]
pub enum ClaimError {
    #[error("{0}")]
    Refused(String),
    #[error(transparent)]
    Email(#[from] EmailError),
    #[error("the claim ledger is unreachable: {0}")]
    Store(String),
}

impl From<StoreError> for ClaimError {
    fn from(error: StoreError) -> Self {
        Self::Store(error.to_string())
    }
}

/// The claim is the machine's state and the seam the verifier sits behind: the terminal earns its
/// verified stamp by confirming a code, the browser arrives already verified, and every write that
/// could race another attempt is a conditional one whose loser says so.
#[derive(Debug, Clone)]
pub struct ClaimWorkflow {
    pub store: OnboardStore,
    pub email_policy: SignupEmailPolicy,
    pub verifier: Verifier,
    pub claim_ttl: Duration,
}

impl ClaimWorkflow {
    pub fn new(store: OnboardStore, verifier: Verifier) -> Self {
        Self {
            store,
            email_policy: SignupEmailPolicy::default(),
            verifier,
            claim_ttl: Duration::minutes(CLAIM_TTL_MINUTES),
        }
    }

    /// Open a claim and ask WorkOS to mail its code. A verifier that refuses takes the claim with
    /// it, so the session is free for the member's next attempt rather than holding a claim whose
    /// code was never sent.
    pub async fn start(
        &self,
        email: &str,
        surface: &str,
        surface_ref: &str,
    ) -> Result<String, ClaimError> {
        let signup = self.email_policy.validate(email)?;
        let claim = self.claim(&signup, surface, surface_ref, None);
        self.store.insert_claim(&claim).await?;
        if let Err(VerificationError(message)) = self.verifier.begin(&claim.email).await {
            self.store.delete_claim(claim.claim_id).await?;
            tracing::warn!(
                target: "ufo_control::claim",
                "onboard.verify.begin_failed domain={} surface={surface}", signup.domain
            );
            return Err(ClaimError::Refused(message));
        }
        Ok(signup.domain)
    }

    /// Grade one code against a live claim. Each refusal takes the claim only if this attempt is the
    /// one that got there first: the loser of a race says so rather than reporting a verdict another
    /// attempt reached.
    pub async fn verify(&self, claim: &OnboardClaim, code: &str) -> Result<(), ClaimError> {
        if Utc::now() >= claim.expires_at {
            return Err(
                if self.store.delete_unverified_claim(claim.claim_id).await? {
                    ClaimError::Refused(CODE_EXPIRED.to_string())
                } else {
                    ClaimError::Refused(VERIFICATION_CHANGED.to_string())
                },
            );
        }
        let confirmed = match self.verifier.confirm(&claim.email, code).await {
            Ok(confirmed) => confirmed,
            Err(VerificationError(message)) => {
                return Err(
                    if self.store.delete_unverified_claim(claim.claim_id).await? {
                        ClaimError::Refused(message)
                    } else {
                        ClaimError::Refused(VERIFICATION_CHANGED.to_string())
                    },
                );
            }
        };
        if !confirmed {
            return Err(ClaimError::Refused(CODE_INCORRECT.to_string()));
        }
        if !self.store.mark_verified(claim.claim_id).await? {
            return Err(ClaimError::Refused(VERIFICATION_CHANGED.to_string()));
        }
        Ok(())
    }

    /// The browser's claim: WorkOS has already answered, so the claim is stamped as it is written. A
    /// second callback for a session that already holds a live claim resolves that claim rather than
    /// opening a second one, so the member lands where the first one left off.
    pub async fn admit_verified(
        &self,
        email: &str,
        surface: &str,
        surface_ref: &str,
    ) -> Result<OnboardClaim, ClaimError> {
        let signup = self.email_policy.validate(email)?;
        if let Some(existing) = self.store.live_claim(surface, surface_ref).await? {
            return Ok(existing);
        }
        let claim = self.claim(&signup, surface, surface_ref, Some(Utc::now()));
        self.store.insert_claim(&claim).await?;
        Ok(claim)
    }

    fn claim(
        &self,
        signup: &crate::email::SignupEmail,
        surface: &str,
        surface_ref: &str,
        verified_at: Option<DateTime<Utc>>,
    ) -> OnboardClaim {
        OnboardClaim {
            claim_id: Uuid::new_v4(),
            email: signup.address.clone(),
            email_domain: signup.domain.clone(),
            signup_subject: signup.subject.clone(),
            surface: surface.to_string(),
            surface_ref: surface_ref.to_string(),
            expires_at: Utc::now() + self.claim_ttl,
            verified_at,
            invite_id: None,
        }
    }
}
