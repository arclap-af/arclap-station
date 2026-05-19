# Arclap Station — developer convenience targets.
#
# `make dev`        — spin up backend (mock camera) + frontend in watch mode
# `make test`       — run backend + frontend tests
# `make lint`       — ruff + mypy + eslint + tsc
# `make wheel`      — build the backend wheel locally
# `make frontend`   — build the frontend bundle locally
# `make deb`        — build the .deb (Linux only; requires fpm)
# `make image`      — print the message that the Pi image is built in CI
# `make clean`      — delete dist/, node_modules/, .venv/ etc.

SHELL          := /bin/bash
PYTHON         ?= python3.11
NODE           ?= node
NPM            ?= npm

BACKEND_DIR    := backend
FRONTEND_DIR   := frontend
DIST_DIR       := dist

.PHONY: help dev backend-dev frontend-dev test backend-test frontend-test e2e \
        lint backend-lint frontend-lint typecheck wheel frontend deb image \
        clean clean-backend clean-frontend setup install-dev shellcheck

help: ## Show this help.
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
setup: ## Install dev dependencies for backend + frontend.
	@echo "==> backend deps"
	cd $(BACKEND_DIR) && $(PYTHON) -m venv .venv && \
	  . .venv/bin/activate && pip install --upgrade pip && pip install -e ".[dev]"
	@echo "==> frontend deps"
	cd $(FRONTEND_DIR) && $(NPM) ci

install-dev: setup ## Alias for `setup`.

# ---------------------------------------------------------------------------
# Dev servers
# ---------------------------------------------------------------------------
dev: ## Run backend (mock camera) and frontend (vite dev) concurrently.
	@echo "Backend → http://127.0.0.1:8080   Frontend → http://127.0.0.1:5173"
	@$(MAKE) -j2 backend-dev frontend-dev

backend-dev: ## Backend with --mock-camera and --reload.
	cd $(BACKEND_DIR) && . .venv/bin/activate && \
	  $(PYTHON) -m arclap_station --mock-camera --reload --listen 127.0.0.1:8080

frontend-dev: ## Vite dev server.
	cd $(FRONTEND_DIR) && $(NPM) run dev

# ---------------------------------------------------------------------------
# Tests + lint
# ---------------------------------------------------------------------------
test: backend-test frontend-test ## Run all tests.

backend-test: ## pytest.
	cd $(BACKEND_DIR) && . .venv/bin/activate && pytest -ra

frontend-test: ## vitest (run mode).
	cd $(FRONTEND_DIR) && $(NPM) test -- --run

e2e: ## Playwright happy-path (requires backend running in another shell).
	cd $(FRONTEND_DIR) && npx playwright test

lint: backend-lint frontend-lint shellcheck ## All linters.

backend-lint: ## ruff.
	cd $(BACKEND_DIR) && . .venv/bin/activate && ruff check .

frontend-lint: ## eslint.
	cd $(FRONTEND_DIR) && $(NPM) run lint

typecheck: ## mypy + tsc.
	cd $(BACKEND_DIR) && . .venv/bin/activate && mypy arclap_station
	cd $(FRONTEND_DIR) && $(NPM) run typecheck

shellcheck: ## shellcheck install.sh + maintainer scripts.
	@command -v shellcheck >/dev/null 2>&1 || { echo "shellcheck not installed"; exit 0; }
	shellcheck -S warning install.sh
	shellcheck -S warning packaging/deb/postinst packaging/deb/prerm packaging/deb/postrm

# ---------------------------------------------------------------------------
# Build artifacts
# ---------------------------------------------------------------------------
wheel: ## Build the backend wheel into dist/.
	cd $(BACKEND_DIR) && . .venv/bin/activate && \
	  $(PYTHON) -m pip install --upgrade build && \
	  $(PYTHON) -m build --wheel --outdir ../$(DIST_DIR)
	@ls -la $(DIST_DIR)

frontend: ## Build the frontend production bundle.
	cd $(FRONTEND_DIR) && $(NPM) run build
	mkdir -p $(DIST_DIR)
	tar -C $(FRONTEND_DIR)/dist -czf $(DIST_DIR)/arclap-station-frontend.tar.gz .
	@ls -la $(DIST_DIR)

deb: wheel frontend ## Build the .deb (Linux only; requires fpm).
ifeq ($(shell uname -s),Linux)
	@command -v fpm >/dev/null 2>&1 || { echo "Install fpm: gem install fpm"; exit 1; }
	@bash -c 'tag=$$(git describe --tags --abbrev=0 2>/dev/null || echo v0.0.0); \
	  version=$${tag#v}; \
	  staged=build-deb/pkg; rm -rf "$$staged"; \
	  mkdir -p "$$staged/opt/arclap-station/wheels" "$$staged/var/www/arclap" \
	           "$$staged/etc/systemd/system" "$$staged/etc/udev/rules.d" \
	           "$$staged/etc/avahi/services" "$$staged/etc/caddy" \
	           "$$staged/usr/local/sbin"; \
	  cp $(DIST_DIR)/*.whl "$$staged/opt/arclap-station/wheels/"; \
	  tar -xzf $(DIST_DIR)/arclap-station-frontend.tar.gz -C "$$staged/var/www/arclap"; \
	  cp systemd/*.service systemd/*.socket systemd/*.timer "$$staged/etc/systemd/system/"; \
	  cp udev/50-arclap-camera.rules "$$staged/etc/udev/rules.d/"; \
	  cp avahi/arclap-station.service "$$staged/etc/avahi/services/"; \
	  cp caddy/Caddyfile.template "$$staged/etc/caddy/Caddyfile.template"; \
	  cp install.sh "$$staged/usr/local/sbin/arclap-station-installer"; \
	  chmod +x "$$staged/usr/local/sbin/arclap-station-installer"; \
	  fpm -s dir -t deb -n arclap-station -v "$$version" -a arm64 \
	      --description "Arclap Station — DSLR control plane for Raspberry Pi 5" \
	      --url "https://github.com/arclap-af/arclap-station" --license "Apache-2.0" \
	      --maintainer "engineering@arclap.ch" --vendor "Arclap AG" \
	      --after-install packaging/deb/postinst \
	      --before-remove  packaging/deb/prerm \
	      --after-remove   packaging/deb/postrm \
	      --depends "python3.11" --depends "python3.11-venv" \
	      --depends "libgphoto2-6" --depends "gphoto2" --depends "ca-certificates" \
	      --depends "curl" --depends "jq" --deb-recommends caddy --deb-recommends avahi-daemon \
	      --chdir "$$staged" -p "$(DIST_DIR)/arclap-station_$${version}_arm64.deb" .'
	@ls -la $(DIST_DIR)
else
	@echo "deb target is Linux-only — current OS is $$(uname -s). The release CI builds it."
	@exit 1
endif

image: ## Pi image build is CI-only — see .github/workflows/release.yml.
	@echo "The Raspberry Pi image is built in CI by .github/workflows/release.yml (job: build-pi-image)."
	@echo "It uses pi-gen and a privileged loopback mount which doesn't work reliably outside Linux CI."

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
clean: clean-backend clean-frontend ## Remove all build artifacts.
	rm -rf $(DIST_DIR) build-deb

clean-backend:
	rm -rf $(BACKEND_DIR)/dist $(BACKEND_DIR)/build $(BACKEND_DIR)/.pytest_cache \
	       $(BACKEND_DIR)/.mypy_cache $(BACKEND_DIR)/.ruff_cache $(BACKEND_DIR)/.venv
	find $(BACKEND_DIR) -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

clean-frontend:
	rm -rf $(FRONTEND_DIR)/node_modules $(FRONTEND_DIR)/dist $(FRONTEND_DIR)/.vite \
	       $(FRONTEND_DIR)/playwright-report $(FRONTEND_DIR)/test-results
