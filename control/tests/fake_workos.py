"""A verifier standing in for WorkOS: it records what was asked of it and answers from what a test
set up. WorkOS is the dependency it replaces, never the thing a test asserts."""

from dataclasses import dataclass, field

from ufo_control.gateway_workos import VerificationError

MAGIC_CODE = "654321"


@dataclass
class FakeVerifier:
    """`faults` are the addresses WorkOS answers with something other than a grade: the real
    verifier refuses a code it cannot get an answer about, and returns False for every code WorkOS
    declines to redeem, so a fault is the only thing `confirm` raises for."""

    exchanges: dict[str, str] = field(default_factory=dict)
    begun: list[str] = field(default_factory=list)
    codes: dict[str, str] = field(default_factory=dict)
    faults: set[str] = field(default_factory=set)
    begin_fails: bool = False

    def authorization_url(self, state: str) -> str:
        return f"/authkit-test?state={state}"

    async def exchange(self, code: str) -> str:
        try:
            return self.exchanges[code]
        except KeyError:
            raise VerificationError("Sign-in failed. Try again.") from None

    async def begin(self, email: str) -> None:
        if self.begin_fails:
            raise VerificationError("Could not send the verification code. Try again.")
        self.begun.append(email)
        self.codes[email] = MAGIC_CODE

    async def confirm(self, email: str, code: str) -> bool:
        if email in self.faults:
            raise VerificationError("Sign-in failed. Try again.")
        return self.codes.get(email) == code
