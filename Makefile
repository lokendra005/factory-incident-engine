.PHONY: help setup demo simulate ingest eval regression status test serve clean docker

PY ?= python3

help:  ## show targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

setup:  ## install runtime + dev dependencies
	$(PY) -m pip install -r requirements.txt

demo:  ## run the whole story end-to-end (simulate -> ... -> regression)
	$(PY) -m fie.cli demo

simulate:  ## write the messy raw feed + golden set
	$(PY) -m fie.cli simulate --reset

ingest:  ## ingest raw feed into the store (crash-safe, resumable)
	$(PY) -m fie.cli ingest

eval:  ## score the current engine against the golden set (fails on regression)
	$(PY) -m fie.cli eval

regression:  ## side-by-side replay: v1.1.0 vs v1.2.0
	$(PY) -m fie.cli regression

status:  ## store + data-quality snapshot
	$(PY) -m fie.cli status

test:  ## run the test suite
	$(PY) -m pytest -q

serve:  ## launch the web UI at http://127.0.0.1:8000
	$(PY) -m fie.cli serve

start:  ## proper startup: build data if needed, then serve the UI
	./scripts/start.sh

stop:  ## stop the running UI server (default port 8000)
	./scripts/stop.sh

clean:  ## remove generated data + caches
	rm -rf data/raw data/runs data/golden data/*.db data/*.db-* \
	       .pytest_cache **/__pycache__ 2>/dev/null || true

docker:  ## build + run the demo container (UI on :8000)
	docker compose up --build
