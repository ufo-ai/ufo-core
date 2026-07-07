"""Public re-export: the skill value object and the in-memory parser an extension contributing
runtime skills declares against.

`ufo.sdk` is a package of thin re-export modules with an empty `__init__.py` (the gate bans
code in any `__init__.py`), so the public surface lives in named modules like this one."""

from ufo.skills.runtime import (
    RuntimeSkill as RuntimeSkill,
)
from ufo.skills.runtime import (
    parse_skill_content as parse_skill_content,
)
