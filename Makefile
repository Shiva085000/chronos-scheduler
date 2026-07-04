.PHONY: up down logs seed test test-unit ps

up:            ## build & start the full stack
	docker compose up --build -d

down:          ## stop everything (keeps the pg volume)
	docker compose down

ps:
	docker compose ps

logs:          ## follow api + worker logs
	docker compose logs -f api worker

seed:          ## create demo user + demo jobs
	docker compose exec api python -m app.scripts.seed

test-unit:     ## pure domain-logic tests
	docker compose exec api python -m pytest tests/test_retry_policy.py -v

test:          ## full suite incl. the claim-concurrency integration test
	docker compose exec api sh -c 'TEST_DATABASE_URL=$$DATABASE_URL python -m pytest tests/ -v'
