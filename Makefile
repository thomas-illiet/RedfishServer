UV ?= uv
export UV_CACHE_DIR ?= $(CURDIR)/.cache/uv

.PHONY: run up down check test format smoke build verify release
run:
	$(UV) run --frozen python -m redfish_mock --multi-profile --port 8000
up:
	docker compose up --build -d --wait
down:
	docker compose down --remove-orphans
check:
	$(UV) run --frozen ruff check .
	$(UV) run --frozen ruff format --check .
	$(UV) run --frozen ty check
	$(UV) run --frozen python -m scripts.structure_gate
	$(MAKE) test
test:
	$(UV) run --frozen python -m pytest
	$(UV) run --frozen python -m scripts.coverage_gate
format:
	$(UV) run --frozen ruff check --fix .
	$(UV) run --frozen ruff format .
smoke:
	$(UV) run --frozen python -m scripts.smoke $(ARGS)
build:
	$(UV) build
verify: check
	$(UV) run --frozen --group audit python -m scripts.delivery verify
release: verify
	$(UV) run --frozen --group audit python -m scripts.delivery package
