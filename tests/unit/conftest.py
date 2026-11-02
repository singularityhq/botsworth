"""Shared pytest fixtures for tests/unit (QF owns this file)."""

import os
import time

from qstore import qroots, qstore  # noqa: F401  (fixtures registered by import)

# The suite expects the maintainer's zone. The default zone is the machine's zone (resolve_tz reads
# BOTSWORTH_TZ, then TZ), so pin TZ here, at import, before any test or child process reads it.
TEST_TZ = "Asia/Kolkata"
os.environ.pop("BOTSWORTH_TZ", None)
os.environ["TZ"] = TEST_TZ
time.tzset()
