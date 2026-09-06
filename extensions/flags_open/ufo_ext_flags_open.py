"""Every flag on: the feature-flag backend a development or eval stack selects.

The fleet reads its flags from Flagship, and a call site withholding a feature reads closed where
nothing answers — so a stack with no flag service offers only what its code defaults to, which for
every flagged feature is nothing. A local serve or an eval stack wants the opposite: the whole
product, so what is built can be driven. Selecting `[flags] backend = "open"` answers every flag
read `true`, through the same OpenFeature client the fleet reads Flagship through, so the code path
a flag gates is the one exercised and no call site learns which environment it is in.

A flag that selects between two shapes rather than offering a withheld one says so where it is
declared: a `FlagSpec` with `open=False` reads `false` here, so the stack lands on the shape the
fleet serves. An undeclared key reads `true`, since only a declared flag can have said otherwise."""

from collections.abc import Mapping, Sequence

from openfeature.evaluation_context import EvaluationContext
from openfeature.flag_evaluation import FlagResolutionDetails, FlagValueType, Reason
from openfeature.provider import AbstractProvider, Metadata

from ufo.sdk.flags import SERVED_FALSE, SERVED_TRUE
from ufo.sdk.manifest import FlagProviderSpec, FlagSpec, Manifest

NAME = "flags_open"
VERSION = "0.1.0"
FLAG_BACKEND = "open"
ON_VARIANT = "on"
OFF_VARIANT = "off"


class OpenProvider(AbstractProvider):
    """Answers every string flag with the served spelling of true, except the keys declared
    closed under this backend."""

    def __init__(self, closed: frozenset[str]) -> None:
        self.closed = closed

    def get_metadata(self) -> Metadata:
        return Metadata(name=FLAG_BACKEND)

    def resolve_boolean_details(
        self,
        flag_key: str,
        default_value: bool,
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[bool]:
        on = flag_key not in self.closed
        return FlagResolutionDetails(
            value=on, variant=ON_VARIANT if on else OFF_VARIANT, reason=Reason.STATIC
        )

    def resolve_string_details(
        self,
        flag_key: str,
        default_value: str,
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[str]:
        on = flag_key not in self.closed
        return FlagResolutionDetails(
            value=SERVED_TRUE if on else SERVED_FALSE,
            variant=ON_VARIANT if on else OFF_VARIANT,
            reason=Reason.STATIC,
        )

    def resolve_integer_details(
        self,
        flag_key: str,
        default_value: int,
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[int]:
        return FlagResolutionDetails(value=default_value, reason=Reason.DEFAULT)

    def resolve_float_details(
        self,
        flag_key: str,
        default_value: float,
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[float]:
        return FlagResolutionDetails(value=default_value, reason=Reason.DEFAULT)

    def resolve_object_details(
        self,
        flag_key: str,
        default_value: Sequence[FlagValueType] | Mapping[str, FlagValueType],
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[Sequence[FlagValueType] | Mapping[str, FlagValueType]]:
        return FlagResolutionDetails(value=default_value, reason=Reason.DEFAULT)


def build(cache_ttl_seconds: float, declared: Mapping[str, FlagSpec]) -> OpenProvider:
    return OpenProvider(frozenset(key for key, spec in declared.items() if not spec.open))


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        flag_providers=(FlagProviderSpec(backend=FLAG_BACKEND, build=build),),
    )
