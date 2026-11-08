UV ?= uv
export UV_CACHE_DIR ?= $(CURDIR)/.cache/uv

.PHONY: run up down check test format smoke verify release
run:
	$(UV) run --frozen python -m redfish_mock --fleet
up:
	docker compose up --build -d --wait
down:
	docker compose --profile extras down
check:
	$(UV) run --frozen ruff check .
	$(UV) run --frozen ruff format --check .
	$(UV) run --frozen ty check
	$(MAKE) test
test:
	$(UV) run --frozen pytest
	$(UV) run --frozen python -m scripts.coverage_gate
format:
	$(UV) run --frozen ruff check --fix .
	$(UV) run --frozen ruff format .
smoke:
	$(UV) run --frozen python -m scripts.smoke $(ARGS)
verify: check
	$(UV) run --frozen --group audit python -m scripts.delivery verify
release: verify
	$(UV) run --frozen --group audit python -m scripts.delivery package
