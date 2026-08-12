.DEFAULT_GOAL := help

WEB := extensions/web/frontend
DEBUGGER := extensions/debugger/frontend
EMAIL ?= $(shell git config user.email)
T ?=
FILE ?=

.PHONY: help install reinstall build init serve chat portal stack stack-down db \
	check fmt test test-one test-control test-web test-integration

help: ## List targets
	@grep -hE '^[a-z][a-z-]*:.*## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*## "}{printf "  %-16s %s\n", $$1, $$2}'

install: $(WEB)/node_modules $(DEBUGGER)/node_modules ## Sync Python deps, npm trees, git hooks
	uv sync
	uv run pre-commit install

reinstall: ## Rebuild the wheel into the venv — single-file extensions are copied, not linked
	uv sync --reinstall-package ufo

build: $(WEB)/node_modules $(DEBUGGER)/node_modules ## Build the portal and debugger pages
	npm --prefix $(WEB) run build
	npm --prefix $(DEBUGGER) run build

$(WEB)/node_modules: $(WEB)/package-lock.json
	npm --prefix $(WEB) ci
	@touch $@

$(DEBUGGER)/node_modules: $(DEBUGGER)/package-lock.json
	npm --prefix $(DEBUGGER) ci
	@touch $@

init: ## Write ufo.toml, apply the schema, onboard the workspace (EMAIL=you@example.com)
	@test -n "$(EMAIL)" || { echo "EMAIL is required: make init EMAIL=you@example.com"; exit 1; }
	uv run ufoctl init --email $(EMAIL)

serve: ## Run surfaces, workers, and the embedded egress proxy (needs ANTHROPIC_API_KEY)
	uv run ufoctl serve

chat: ## Talk to the agent; sessions persist across runs
	uv run ufoctl chat

portal: ## Open the portal in a browser, signed in with this machine's CLI token
	uv run ufoctl portal

stack: build ## Bring up the hosted topology in Docker — gateway :8080, serve :8710
	docker compose up

stack-down: ## Stop the hosted stack
	docker compose down

db: ## Start only Postgres — the Postgres half of the test and eval matrix, on :5541
	docker compose up -d postgres

check: ## Run every static gate CI runs — ruff, gates.py, mypy, control
	uv run pre-commit run --all-files --hook-stage pre-push

fmt: ## Format the tree
	uv run ruff format

test: ## Run the parallel suite; T=<paths> narrows it to a focused run
	uv run pytest $(if $(T),,-n auto) -m "not serial and not integration and not docker" -q $(T)

test-one: ## Run one file or node id serially (FILE=path) — xdist only pays above ~100 tests
	@test -n "$(FILE)" || { echo "FILE is required: make test-one FILE=core/tests/test_hooks.py"; exit 1; }
	uv run pytest -m "not integration and not docker" -q $(FILE)

test-control: ## Run the control (gateway) suite
	uv run --project control pytest control/tests --ignore=control/tests/test_rls.py -q

test-web: $(WEB)/node_modules ## Run the portal's vitest suite
	npm --prefix $(WEB) test

test-integration: ## Run the serial, docker, and live-dependency pass (needs Docker and Postgres)
	UFO_INTEGRATION_REQUIRED=1 uv run pytest -rs -m "serial or integration or docker"
