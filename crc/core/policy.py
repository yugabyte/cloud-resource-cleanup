"""Pure filtering policy for CRC cleaners.

Every function here implements exactly one filtering variant. Variants that behave differently are
separate, explicitly named functions: two different semantics are never merged behind
a flag. Nothing here reads the environment, the clock or any SDK; callers fetch data,
call :func:`normalize_tags`, and let these functions decide.

The functions live in ``policy_tags``, ``policy_text``, ``policy_managed`` and ``policy_age``;
this module re-exports all of them, so ``from crc.core import policy`` keeps working.
"""

from crc.core.policy_age import *  # noqa: F403
from crc.core.policy_managed import *  # noqa: F403
from crc.core.policy_tags import *  # noqa: F403
from crc.core.policy_text import *  # noqa: F403
