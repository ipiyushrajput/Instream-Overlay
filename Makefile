.PHONY: demo origin backend frontend setup stop

PYTHON ?= python3
ORIGIN_PORT ?= 8100
BACKEND_PORT ?= 8000

setup:
	./scripts/setup.sh

# Run origin simulator + backend + frontend together. Ctrl-C stops all.
demo:
	@echo "Starting origin simulator, backend, and frontend…"
	@trap 'kill 0' EXIT; \
	$(PYTHON) tools/gen_origin.py --port $(ORIGIN_PORT) & \
	( cd backend && . .venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port $(BACKEND_PORT) ) & \
	( cd frontend && npm run dev ) & \
	wait

origin:
	$(PYTHON) tools/gen_origin.py --port $(ORIGIN_PORT)

backend:
	cd backend && . .venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port $(BACKEND_PORT)

frontend:
	cd frontend && npm run dev

stop:
	-pkill -f "uvicorn app.main" || true
	-pkill -f "gen_origin.py" || true
