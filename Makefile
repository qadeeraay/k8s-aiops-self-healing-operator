.PHONY: help test demo lint clean run

SHELL := /bin/bash
PYTHON := python3

help:
	@echo "================================================================================"
	@echo "   AUTONOMOUS KUBERNETES SELF-HEALING AIOPS OPERATOR (MAKEFILE)                 "
	@echo "================================================================================"
	@echo "Available commands:"
	@echo "  make test      - Run automated unit and regression tests"
	@echo "  make demo      - Run interactive chaos fault-injection simulation"
	@echo "  make lint      - Run code style and syntax checks"
	@echo "  make clean     - Remove cached reports and temporary files"
	@echo "  make run       - Start the live AIOps controller (requires kubectl/cluster)"

test:
	@echo "==> Running AIOps test suite..."
	$(PYTHON) -m unittest discover -s tests -v

demo:
	@echo "==> Running live chaos injection and self-healing simulation..."
	$(PYTHON) chaos_suite/run_demo_simulation.py

lint:
	@echo "==> Checking Python syntax and linting..."
	$(PYTHON) -m py_compile $$(find aiops_operator chaos_suite tests -name "*.py")
	@flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics || true

clean:
	@echo "==> Cleaning temporary artifacts..."
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@rm -rf reports/*.md *.log .pytest_cache
	@echo "==> Cleaned."
