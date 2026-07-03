import pytest

from gw_geo.common import config as _config

# Hermetic isolation: the suite must never read a developer's local `.env`. `Settings` is configured
# with `env_file=".env"`, so a real `.env` (pgvector selected + live API keys) would otherwise leak
# into every `Settings()` a test builds -- even for fields not passed explicitly -- breaking the
# suite's default/key assumptions (e.g. `vector_store`, or which engines have keys). Disable env-file
# loading for the whole test session; explicit kwargs and `monkeypatch.setenv` still work, only the
# on-disk `.env` is ignored.
_config.Settings.model_config["env_file"] = None


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
