.DEFAULT_GOAL := help

WEB := extensions/web/frontend
DEBUGGER := extensions/debugger/frontend
EMAIL ?= $(shell git config user.email)
T ?=
FILE ?=
WT ?=
SHARD ?=
STACK ?= 1
STACKS := 1 2 3 4 5
SHELL_NAME ?= sidebar
SHELL_NAMES := sidebar lanes
PYTEST_TIMEOUT_SECONDS := 120

ifeq ($(filter $(STACK),$(STACKS)),)
$(error STACK must be one of: $(STACKS))
endif

ifeq ($(filter $(SHELL_NAME),$(SHELL_NAMES)),)
$(error SHELL_NAME must be one of: $(SHELL_NAMES))
endif

STACK_OFFSET := $(shell expr \( $(STACK) - 1 \) \* 100)
STACK_NAME := ufo-$(STACK)
STACK_HOST := $(STACK_NAME).localhost
GIT_COMMON_DIR := $(shell git rev-parse --path-format=absolute --git-common-dir)
REPOSITORY_ROOT := $(shell dirname "$(GIT_COMMON_DIR)")
UFO_PG_PORT ?= $(shell expr 15541 + $(STACK_OFFSET))
UFO_REDIS_PORT ?= $(shell expr 15543 + $(STACK_OFFSET))
UFO_STACK_PORT_HOST ?= $(shell expr 18080 + $(STACK_OFFSET))
UFO_SERVE_PORT_HOST ?= $(shell expr 18710 + $(STACK_OFFSET))
UFO_INGRESS_PORT_HOST ?= $(shell expr 18100 + $(STACK_OFFSET))
UFO_WORKSPACE_ROOT ?= $(REPOSITORY_ROOT)/.local/$(STACK_NAME)/workspaces
# `portal_page` serves the sidebar shell, so the slot's dev server serves that shell by default.
WEB_VITE_CONFIG := $(if $(filter sidebar,$(SHELL_NAME)),sidebar/vite.config.ts,vite.config.ts)
STACK_ENV := UFO_DEV_IMAGE=$(STACK_NAME)-dev UFO_STACK_HOST=$(STACK_HOST) \
	UFO_PG_PORT=$(UFO_PG_PORT) UFO_REDIS_PORT=$(UFO_REDIS_PORT) \
	UFO_STACK_PORT_HOST=$(UFO_STACK_PORT_HOST) \
	UFO_SERVE_PORT_HOST=$(UFO_SERVE_PORT_HOST) \
	UFO_INGRESS_PORT_HOST=$(UFO_INGRESS_PORT_HOST) \
	UFO_WORKSPACE_ROOT="$(UFO_WORKSPACE_ROOT)" \
	UFO_WEB_VITE_CONFIG=$(WEB_VITE_CONFIG)
COMPOSE := $(STACK_ENV) docker compose --project-name $(STACK_NAME)
STACK_ORIGIN := http://$(STACK_HOST):$(UFO_STACK_PORT_HOST)
# Where `make signin` opens the signed-in portal. Chrome, because it treats `*.localhost` as a
# secure origin and holds the portal's `Secure` cookie there; Safari drops it.
BROWSER ?= open -a "Google Chrome" %s

.PHONY: help install reinstall build init serve portal setup stack stack-down stack-logs signin db \
	check fmt test test-one test-control test-preview test-client test-client-load \
	cover-client bench-client test-web test-integration

help: ## List targets
	@grep -hE '^[a-z][a-z-]*:.*## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*## "}{printf "  %-16s %s\n", $$1, $$2}'

install: $(WEB)/node_modules $(DEBUGGER)/node_modules ## Sync Python deps, pnpm trees, git hooks
	uv sync
	uv run pre-commit install

reinstall: ## Rebuild the wheel into the venv — single-file extensions are copied, not linked
	uv sync --reinstall-package ufo

build: $(WEB)/node_modules $(DEBUGGER)/node_modules ## Build the client, portal, app pages and SDK, and debugger
	cargo build --manifest-path client/Cargo.toml --locked
	pnpm -C $(WEB) run build
	pnpm -C $(DEBUGGER) run build

$(WEB)/node_modules: $(WEB)/pnpm-lock.yaml
	pnpm -C $(WEB) install --frozen-lockfile
	@touch $@

$(DEBUGGER)/node_modules: $(DEBUGGER)/pnpm-lock.yaml
	pnpm -C $(DEBUGGER) install --frozen-lockfile
	@touch $@

init: ## Write ufo.toml, apply the schema, onboard the workspace (EMAIL=you@example.com)
	@test -n "$(EMAIL)" || { echo "EMAIL is required: make init EMAIL=you@example.com"; exit 1; }
	uv run ufoctl init --email $(EMAIL)

serve: ## Run surfaces + workers, no sandbox egress (needs UFO_ANTHROPIC_API_KEY; `make stack` adds the ufo-egress rig)
	uv run ufoctl serve

portal: ## Open the portal in a browser, signed in with this machine's CLI token
	uv run ufoctl portal

setup: ## Set up local developer env
	@set -e; if test -e .env || test -L .env; then \
		echo ".env already exists; left unchanged."; \
	else \
		umask 077; \
		set -C; \
		cat .env.template > .env; \
		echo "Created .env. Set API keys, then run make stack."; \
	fi

stack: ## Bring up stack 1-5 in Docker; the portal and the app pages reload on every frontend change (STACK=1, SHELL_NAME=sidebar|lanes); WT=<worktree> copies .env there and runs in it
	@test -f .env || { echo ".env is required: configure it before running make stack, with 'make setup'" >&2; exit 1; }
ifneq ($(WT),)
	@set -e; \
		wt_path="$$(git worktree list --porcelain | sed -n 's/^worktree //p' | grep -Fxv -- "$$(pwd)" | grep -m1 -- "$(WT)" || true)"; \
		test -n "$$wt_path" || { echo "no other git worktree matches '$(WT)': see git worktree list" >&2; exit 1; }; \
		cp .env "$$wt_path/.env"; \
		exec $(MAKE) -C "$$wt_path" stack STACK=$(STACK) WT=
else
	@set -e; \
		for name in ANTHROPIC_API_KEY OPENAI_API_KEY; do \
			if grep -Eq "^(export )?$$name=" .env; then \
				echo ".env sets $$name, which every tool reading .env picks up — rename it to UFO_$$name." >&2; exit 1; \
			fi; \
		done; \
		names="$$(cut -d= -f1 .env.template)"; \
		for name in $$names; do unset "$$name"; done; \
		set -a; . ./.env; set +a; \
		for name in $$names; do \
			value="$$(printenv "$$name" || true)"; \
			test -n "$$value" || { echo "$$name is empty in .env." >&2; exit 1; }; \
		done; \
		$(MAKE) build; \
		echo "Sign in: $(STACK_ORIGIN)/login, or seat UFO_DEV_EMAIL and open the portal: make signin STACK=$(STACK)"; \
		echo "Portal: $(STACK_ORIGIN)/surface/web. The shell and the app pages reload on every frontend change."; \
		$(COMPOSE) up --build
endif

stack-down: ## Stop stack 1-5 (STACK=1)
	$(COMPOSE) down

stack-logs: ## Follow gateway logs for stack 1-5 (STACK=1)
	$(COMPOSE) logs --follow gateway

signin: ## Seat UFO_DEV_EMAIL on stack 1-5 through the console sign-in and open the portal in BROWSER (STACK=1)
	@set -e; test -f .env || { echo ".env is required: configure it before running make signin, with 'make setup'" >&2; exit 1; }; \
		set -a; . ./.env; set +a; \
		test -n "$$UFO_DEV_EMAIL" || { echo "UFO_DEV_EMAIL is empty in .env: the address make signin seats." >&2; exit 1; }; \
		BROWSER='$(BROWSER)' uv run python dev/signin.py --origin $(STACK_ORIGIN) --email "$$UFO_DEV_EMAIL"

db: ## Start only Postgres for tests and evals on :5541
	docker compose up -d postgres

check: ## Run every static gate CI runs — ruff, gates.py, mypy, control
	uv run pre-commit run --all-files --hook-stage pre-push

fmt: ## Format the tree
	uv run ruff format

test: ## Run the parallel suite; T=<paths> narrows it; SHARD=1/10 runs one slice
	uv run pytest $(if $(T),,-n auto) $(if $(SHARD),--shard $(SHARD)) \
		--timeout $(PYTEST_TIMEOUT_SECONDS) --timeout-method thread \
		-m "not serial and not integration and not docker" -q $(T)

test-one: reinstall ## Run one file or node id serially (FILE=path) — xdist only pays above ~100 tests
	@test -n "$(FILE)" || { echo "FILE is required: make test-one FILE=core/tests/test_hooks.py"; exit 1; }
	uv run pytest -m "not integration and not docker" -q $(FILE)

control-pg: ## Start the control suite's Postgres on :5549
	docker rm -f ufo-control-rust-pg >/dev/null 2>&1 || true
	docker run -d --rm --name ufo-control-rust-pg -e POSTGRES_USER=ufo -e POSTGRES_PASSWORD=ufo \
		-e POSTGRES_DB=ufo -p 127.0.0.1:5549:5432 pgvector/pgvector:pg17
	until docker exec ufo-control-rust-pg pg_isready -U ufo >/dev/null 2>&1; do sleep 1; done

test-control: ## Run the control (gateway) suite — needs `make control-pg`
	cd servers/control && cargo test

check-control: ## Run the control crate's static gates — fmt and clippy
	cd servers/control && cargo fmt --check && cargo clippy --all-targets -- -D warnings

test-preview: ## Run the preview renderer suite — needs soffice and preview setup scripts
	@lib=$$(ls servers/preview/.pdfium/libpdfium.* 2>/dev/null | head -n1); \
	test -n "$$lib" || { echo "missing servers/preview/.pdfium — run servers/preview/scripts/fetch-pdfium.sh first" >&2; exit 1; }; \
	profile=servers/preview/.soffice-profile; \
	if ! test -s "$$profile/.ufo-preview-version" \
		|| ! test -s "$$profile/user/extensions/buildid" \
		|| ! grep -q 'ooSetupLastVersion' "$$profile/user/registrymodifications.xcu"; then \
		echo "missing $$profile — run servers/preview/scripts/seed-soffice-profile.sh $$profile first" >&2; \
		exit 1; \
	fi; \
	UFO_PREVIEW_PDFIUM_LIB="$$PWD/$$lib" UFO_PREVIEW_SOFFICE_PROFILE="$$PWD/$$profile" \
	sh -c 'cd servers/preview && cargo test -- --include-ignored --test-threads=4'

check-preview: ## Run the preview crate's static gates — fmt and clippy
	cd servers/preview && cargo fmt --check && cargo clippy --all-targets -- -D warnings

test-client: ## Run the client crate's suite
	cd client && cargo nextest run

test-client-load: ## Run the client crate's suite with every core busy — hunts wall-clock fragility
	cd client && hogs=$$(( $$(getconf _NPROCESSORS_ONLN) * 2 )); pids=""; i=0; \
	while [ $$i -lt $$hogs ]; do (while :; do :; done) & pids="$$pids $$!"; i=$$((i + 1)); done; \
	cargo nextest run; status=$$?; kill $$pids 2>/dev/null; exit $$status

cover-client: ## Measure the client crate's coverage — fails below the line floor
	cd client && cargo llvm-cov nextest --branch --fail-under-lines 89

bench-client: ## Benchmark the client crate
	cd client && cargo bench

check-client: ## Run the client crate's static gates — fmt and clippy
	cd client && cargo fmt --check && cargo clippy --all-targets -- -D warnings

test-web: $(WEB)/node_modules ## Run the portal's vitest suite
	pnpm -C $(WEB) test

test-integration: ## Run the serial, docker, and live-dependency pass (needs Docker and Postgres); SHARD=1/3 runs one slice
	UFO_INTEGRATION_REQUIRED=1 uv run pytest -rs -m "serial or integration or docker" $(if $(SHARD),--shard $(SHARD))
