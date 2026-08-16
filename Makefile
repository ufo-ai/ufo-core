.DEFAULT_GOAL := help

WEB := extensions/web/frontend
DEBUGGER := extensions/debugger/frontend
EMAIL ?= $(shell git config user.email)
T ?=
FILE ?=
STACK ?= 1
STACKS := 1 2 3 4 5

ifeq ($(filter $(STACK),$(STACKS)),)
$(error STACK must be one of: $(STACKS))
endif

STACK_OFFSET := $(shell expr \( $(STACK) - 1 \) \* 100)
STACK_NAME := ufo-$(STACK)
STACK_HOST := $(STACK_NAME).localhost
UFO_PG_PORT ?= $(shell expr 15541 + $(STACK_OFFSET))
UFO_REDIS_PORT ?= $(shell expr 15543 + $(STACK_OFFSET))
UFO_GATEWAY_PORT_HOST ?= $(shell expr 18080 + $(STACK_OFFSET))
UFO_SERVE_PORT_HOST ?= $(shell expr 18710 + $(STACK_OFFSET))
STACK_ENV := UFO_DEV_IMAGE=$(STACK_NAME)-dev UFO_STACK_HOST=$(STACK_HOST) \
	UFO_PG_PORT=$(UFO_PG_PORT) UFO_REDIS_PORT=$(UFO_REDIS_PORT) \
	UFO_GATEWAY_PORT_HOST=$(UFO_GATEWAY_PORT_HOST) \
	UFO_SERVE_PORT_HOST=$(UFO_SERVE_PORT_HOST)
COMPOSE := $(STACK_ENV) docker compose --project-name $(STACK_NAME)

.PHONY: help install reinstall build init serve portal stack stack-down stack-logs db \
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

portal: ## Open the portal in a browser, signed in with this machine's CLI token
	uv run ufoctl portal

stack: build ## Bring up stack 1-5 in Docker (STACK=1)
	@echo "Gateway: http://$(STACK_HOST):$(UFO_GATEWAY_PORT_HOST)/login"
	$(COMPOSE) up

stack-down: ## Stop stack 1-5 (STACK=1)
	$(COMPOSE) down

stack-logs: ## Follow gateway logs for stack 1-5 (STACK=1)
	$(COMPOSE) logs --follow gateway

db: ## Start only Postgres for tests and evals on :5541
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

test-integration: ## Run the serial, docker, and live-dependency pass (needs Docker and Postgres); SHARD=1/3 runs one slice
	UFO_INTEGRATION_REQUIRED=1 uv run pytest -rs -m "serial or integration or docker" $(if $(SHARD),--shard $(SHARD))
