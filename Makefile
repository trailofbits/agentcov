TESTS :=

.PHONY: all dev run lint format test coverage doc build check

# If the user selects a specific test pattern to run, set pytest to fail fast
# and only run tests that match the pattern. Otherwise, run the full suite.
ifneq ($(TESTS),)
	TEST_ARGS := -x -k $(TESTS)
else
	TEST_ARGS :=
endif

all:
	@echo "Run my targets individually!"

dev:
	uv sync --group dev

run:
	uv run agentcov $(ARGS)

lint:
	uv sync --group lint
	uv run ruff format --check
	uv run ruff check
	uv run ty check src

format:
	uv sync --group lint
	uv run ruff format
	uv run ruff check --fix

test:
	uv sync --group test
	uv run pytest -q $(TEST_ARGS)

coverage:
	uv sync --group test
	uv run pytest -q --cov=agentcov --cov-report=term-missing

doc:
	@echo "No generated documentation set up"

build:
	uv build

check: lint test
