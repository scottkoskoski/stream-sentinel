.PHONY: lint format test-unit test-integration test-e2e test-contract test-performance test-all docker-up docker-down clean help

DOCKER_COMPOSE := docker compose -f docker/docker-compose.yml

help: ## Show this help message
	@echo "Stream Sentinel - Development Commands"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Code Quality ──────────────────────────────────────────────

lint: ## Run all linters (black, isort, flake8) in check mode
	black --check src/ tests/
	isort --check src/ tests/
	flake8 src/ tests/

format: ## Auto-format code with black and isort
	black src/ tests/
	isort src/ tests/

# ── Testing ───────────────────────────────────────────────────

test-unit: ## Run unit tests (no infrastructure needed)
	pytest tests/unit/ -m unit -v --tb=short

test-integration: ## Run integration tests (requires Docker services)
	pytest tests/integration/ -m integration -v --tb=short

test-e2e: ## Run end-to-end tests (requires Docker services)
	pytest tests/e2e/ -m e2e -v --tb=short

test-contract: ## Run contract tests
	pytest tests/contract/ -v --tb=short

test-performance: ## Run performance benchmarks (requires Docker services)
	pytest tests/performance/ -m performance -v --tb=short

test-all: ## Run all test suites
	pytest tests/unit/ -m unit -v --tb=short
	pytest tests/contract/ -v --tb=short
	pytest tests/integration/ -m integration -v --tb=short
	pytest tests/e2e/ -m e2e -v --tb=short

# ── Infrastructure ────────────────────────────────────────────

docker-up: ## Start all Docker services
	$(DOCKER_COMPOSE) up -d
	@echo "Waiting for services to be healthy..."
	@sleep 5
	@echo "Services started. Use 'make docker-status' to check health."

docker-down: ## Stop and remove all Docker services and volumes
	$(DOCKER_COMPOSE) down -v

docker-status: ## Show status of Docker services
	$(DOCKER_COMPOSE) ps

docker-logs: ## Tail Docker service logs
	$(DOCKER_COMPOSE) logs -f --tail=50

# ── Cleanup ───────────────────────────────────────────────────

clean: ## Remove build artifacts, caches, and generated files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf reports/ .coverage htmlcov/ build/ dist/
