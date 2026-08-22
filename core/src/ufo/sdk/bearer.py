"""Public re-export: the member bearer's verify half — the codec a surface extension checks a
gateway-minted token against (`ufo.auth.bearer` is the one home; the control plane owns minting).
The signing secret stays core's: every function takes the token and resolves `UFO_TOKEN_SECRET`
itself, so no extension ever holds the key."""

from ufo.auth.bearer import (
    LOGIN_PATH as LOGIN_PATH,
)
from ufo.auth.bearer import (
    SESSION_COOKIE as SESSION_COOKIE,
)
from ufo.auth.bearer import (
    verified_claims as verified_claims,
)
from ufo.auth.bearer import (
    verify_token as verify_token,
)
from ufo.auth.bearer import (
    workspace_claim as workspace_claim,
)
