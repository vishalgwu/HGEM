"""What every container-backed fixture shares: the images, and the daemon check.

`fixtures.postgres` (S3.2), `fixtures.neo4j` (S7.1) and `fixtures.redis` (S8.3)
each start a testcontainer and each skips when no Docker daemon answers. The
check was written out three times, and the Redis fixture said the shared version
"arrives when a third fixture wants it" - `fixtures.neo4j` was that third
fixture. It lives in its own module rather than in `fixtures.postgres` so that a
test needing only Redis does not import the Postgres fixture to ask one question.

**The images are here for the same reason: one home, checked.** Each is the
image *and tag* `infra/docker/docker-compose.dev.yml` pins, because a floating
tag makes a green run unreproducible - `RULES.md` §3's argument for pinning model
ids. The Redis fixture shipped `redis:7-alpine` beside a comment claiming exactly
that pin; `7-alpine` floats across every 7.x release, the dev stack pins
`7.4-alpine`, and on a machine that could not pull the floating tag all eight
bucket tests errored at setup. `tests/unit/test_compose_stack.py` now asserts
every image here is one the dev stack pins and one CI pre-pulls.
"""

from __future__ import annotations

import subprocess
from typing import Final

__all__ = ["IMAGES", "NEO4J_IMAGE", "POSTGRES_IMAGE", "REDIS_IMAGE", "docker_available"]

# Also fixes the pgvector version `assertion_hnsw` is built by.
POSTGRES_IMAGE: Final = "pgvector/pgvector:0.8.6-pg16"
# A Neo4j major carries Cypher changes, and `cypher.py` is written against 5.
NEO4J_IMAGE: Final = "neo4j:5.26.30-community"
# The token bucket uses only `HMGET`, `HSET`, `EXPIRE` and `EVALSHA`, all older
# than 7 by years - the pin is about reproducibility, not a recent server.
REDIS_IMAGE: Final = "redis:7.4-alpine"

IMAGES: Final = (POSTGRES_IMAGE, NEO4J_IMAGE, REDIS_IMAGE)


def docker_available() -> bool:
    """Can we talk to a Docker daemon at all?

    Returns:
        True when `docker info` answers within twenty seconds.

    A missing daemon skips the fixture rather than failing it, so a developer
    without Docker can still run everything else - and every fixture's skip
    message names the `GM_TEST_*` variable that points it at an existing server
    instead.
    """
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0
