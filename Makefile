.PHONY: install lint format typecheck test check openapi bench serve docker

install:
	pip install -e ".[dev]"

lint:
	ruff check src tests scripts
	ruff format --check src tests scripts

format:
	ruff check --fix src tests scripts
	ruff format src tests scripts

typecheck:
	mypy

test:
	pytest

check: lint typecheck test

openapi:
	corpnorm openapi -o docs/openapi.yaml
	corpnorm openapi -o docs/openapi.json

bench:
	python scripts/generate_sample.py -n 100000 -o /tmp/corpnorm_sample.csv
	python scripts/benchmark.py /tmp/corpnorm_sample.csv

serve:
	corpnorm serve --host 127.0.0.1 --port 8000

docker:
	docker build -t corpnorm:latest .
