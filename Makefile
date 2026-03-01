.PHONY: install test lint download clean

VENV = VIRTUAL_ENV=.venv

install:
	$(VENV) uv pip install -e ".[dev]"

test:
	.venv/bin/pytest tests/ -v --tb=short

lint:
	.venv/bin/ruff check src/ tests/
	.venv/bin/ruff format --check src/ tests/

download:
	bash sample_data/download_sample.sh

clean:
	rm -rf build/ dist/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
