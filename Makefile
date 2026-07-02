install: ; pip install -e ".[dev]"
test: ; pytest -q
lint: ; ruff check src tests
types: ; mypy src/gw_geo/common
check: lint types test
