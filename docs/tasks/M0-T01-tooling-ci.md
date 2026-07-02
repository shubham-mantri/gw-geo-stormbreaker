# M0-T01 — Project tooling & CI

**Depends on:** none · **Wave:** 0 · **Suggested agent:** general-purpose

**Goal:** Pin dependencies and wire the quality gates (pytest, ruff, mypy) + CI so every later
task runs against a working harness.

**Files:**
- Modify: `pyproject.toml` (add real deps + tool config)
- Create: `Makefile`, `.github/workflows/ci.yml`, `tests/conftest.py`

## Steps

- [ ] **1. Add dependencies** to `pyproject.toml`:

```toml
dependencies = [
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "sqlalchemy>=2.0",
  "alembic>=1.13",
  "psycopg[binary]>=3.2",
  "httpx>=0.27",
  "scipy>=1.13",
  "boto3>=1.34",
]

[project.optional-dependencies]
dev = [
  "pytest>=8", "pytest-asyncio>=0.23", "respx>=0.21", "moto>=5",
  "ruff>=0.6", "mypy>=1.11",
]
```

- [ ] **2. Tool config** — append to `pyproject.toml`:

```toml
[tool.mypy]
python_version = "3.13"
[[tool.mypy.overrides]]
module = "gw_geo.common.*"
strict = true

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **3. `Makefile`:**

```makefile
install: ; pip install -e ".[dev]"
test: ; pytest -q
lint: ; ruff check src tests
types: ; mypy src/gw_geo/common
check: lint types test
```

- [ ] **4. `tests/conftest.py`** — a smoke fixture proving the harness runs:

```python
import pytest

@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
```

- [ ] **5. `.github/workflows/ci.yml`** — Python 3.13, `pip install -e ".[dev]"`, run
  `ruff check`, `mypy src/gw_geo/common`, `pytest -q`.

- [ ] **6. Verify:** `make install && make check` runs green (no tests yet = pytest exits 5;
  configure CI to treat "no tests collected" as pass until T02 lands, or add a trivial
  `tests/test_smoke.py` asserting `True`).

- [ ] **7. Commit:** `chore: pin deps, add ruff/mypy/pytest config + CI`

## Acceptance
- `make install` succeeds on Python 3.13.
- `make check` runs ruff + mypy + pytest with zero errors.
- CI workflow present and green on push.
