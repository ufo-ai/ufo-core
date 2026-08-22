"""Public re-export: the house delivery register block an extension prepends to a direct model
call, so that call writes to the same register the shell and every subagent prompt carry.

`ufo.sdk` is a package of thin re-export modules with an empty `__init__.py` (the gate bans code in
any `__init__.py`), so the public surface lives in named modules like this one."""

from ufo.delivery_register import DELIVERY_REGISTER_BLOCK as DELIVERY_REGISTER_BLOCK
