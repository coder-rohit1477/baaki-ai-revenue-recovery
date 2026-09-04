# Baaki — Phase 1 targets. Credentials come from the environment, never from this file.
#   BAAKI_SUPERUSER_DSN  bootstrap only (roles, database)
#   BAAKI_MIGRATE_DSN    migrations only (baaki_migrate → SET ROLE baaki_owner)
#   BAAKI_WEBHOOK_SECRET bootstrap/secrets.sql only
.PHONY: db-up db-down bootstrap secrets migrate downgrade lock sync test test-integration lint typecheck verify

db-up:
	docker compose up -d --wait
db-down:
	docker compose down

bootstrap:
	@test -n "$$BAAKI_SUPERUSER_DSN" || (echo "BAAKI_SUPERUSER_DSN required" && exit 1)
	psql "$$BAAKI_SUPERUSER_DSN" -v ON_ERROR_STOP=1 \
	  -v owner_pw="$${BAAKI_OWNER_PW:-}" -v migrate_pw="$${BAAKI_MIGRATE_PW:?}" -v app_pw="$${BAAKI_APP_PW:?}" \
	  -v ops_pw="$${BAAKI_OPS_PW:?}" -v agent_pw="$${BAAKI_AGENT_PW:?}" -v sim_pw="$${BAAKI_SIM_PW:?}" \
	  -f bootstrap/roles.sql

secrets:
	@test -n "$$BAAKI_MIGRATE_DSN" || (echo "BAAKI_MIGRATE_DSN required" && exit 1)
	@test -n "$$BAAKI_WEBHOOK_SECRET" || (echo "BAAKI_WEBHOOK_SECRET required" && exit 1)
	psql "$$BAAKI_MIGRATE_DSN" -v ON_ERROR_STOP=1 -v webhook_secret="$$BAAKI_WEBHOOK_SECRET" -f bootstrap/secrets.sql

migrate:
	@test -n "$$BAAKI_MIGRATE_DSN" || (echo "BAAKI_MIGRATE_DSN required" && exit 1)
	uv run alembic upgrade head
downgrade:
	uv run alembic downgrade base

lock:
	uv lock
sync:
	uv sync --frozen

test:
	uv run pytest
test-integration:
	uv run pytest -m network
lint:
	uv run ruff check .
typecheck:
	uv run mypy

verify: lint typecheck test
	uv lock --check
