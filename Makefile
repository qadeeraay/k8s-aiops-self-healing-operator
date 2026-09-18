.PHONY: help build test test-go test-python lint clean run

SHELL := /bin/bash
PYTHON := python3
GO := go

help:
	@echo "================================================================================"
	@echo "   AUTONOMOUS KUBERNETES SELF-HEALING AIOPS OPERATOR (TWO-TIER HYBRID)          "
	@echo "================================================================================"
	@echo "Available commands:"
	@echo "  make build       - Build the Go operator binary into bin/operator"
	@echo "  make test        - Run both Go race-detected tests and Python unit tests"
	@echo "  make test-go     - Run Go unit and concurrency tests with race detection"
	@echo "  make test-python - Run Python heuristic policy engine tests"
	@echo "  make lint        - Run go vet and Python syntax checks"
	@echo "  make clean       - Remove compiled binaries and temporary artifacts"
	@echo "  make run         - Run the operator binary locally with current kubeconfig"

build:
	@echo "==> Compiling Go operator binary..."
	@mkdir -p bin
	$(GO) build -v -o bin/operator ./cmd/operator

test: test-go test-python

test-go:
	@echo "==> Running Go unit and concurrency tests..."
	$(GO) test -v -race ./pkg/watcher ./pkg/remediator

test-python:
	@echo "==> Running Python heuristic policy tests..."
	$(PYTHON) -m unittest discover -s engine -v
	$(PYTHON) -m unittest discover -s tests -v

lint:
	@echo "==> Running Go vet and static checks..."
	$(GO) vet ./...
	@echo "==> Checking Python syntax..."
	$(PYTHON) -m py_compile $$(find engine tests -name "*.py")

clean:
	@echo "==> Cleaning build and temporary artifacts..."
	@rm -rf bin/ /tmp/operator reports/post_mortem_*.md *.log .pytest_cache
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@echo "==> Cleaned."

run: build
	@echo "==> Starting AIOps Operator in local mode..."
	./bin/operator --workers=2 --json-log=true
