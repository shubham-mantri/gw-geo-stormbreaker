install: ; pip install -e ".[dev]"
test: ; pytest -m "not live" -q
lint: ; ruff check src tests
types: ; mypy src/gw_geo/common
check: lint types test
