"""Public re-export: the capability an extension reaches a member by email through, held on its
scoped context as `ctx.email`. The SES client, the suppression union, and the delivery consumer
stay in `servers/control`; this is the seam to them.

`ufo.sdk` is a package of thin re-export modules with an empty `__init__.py`, so the public
surface lives in named modules like this one."""

from ufo.runtime.email import (
    PRODUCT_NEWS as PRODUCT_NEWS,
)
from ufo.runtime.email import (
    TRANSACTIONAL as TRANSACTIONAL,
)
from ufo.runtime.email import (
    EmailRefused as EmailRefused,
)
from ufo.runtime.email import (
    EmailSends as EmailSends,
)
from ufo.runtime.email import (
    EmailUnanswered as EmailUnanswered,
)
