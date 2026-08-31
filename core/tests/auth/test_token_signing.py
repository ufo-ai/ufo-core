import pytest

from ufo.runtime.auth.token_signing import SignedTokenError, sign_token, verify_token


def test_signed_token_round_trip_rejects_tampering_and_malformed_input() -> None:
    token = sign_token(b"secret", b'{"claim":"value"}')

    assert verify_token(token, b"secret") == b'{"claim":"value"}'
    with pytest.raises(SignedTokenError):
        verify_token(token + "x", b"secret")
    with pytest.raises(SignedTokenError):
        verify_token("not-a-token", b"secret")
