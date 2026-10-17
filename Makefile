.PHONY: install test lint demo export-demo eval

PYTHON ?= python

install:
	$(PYTHON) -m pip install -e ".[dev,pipeline]"

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check api core tests scripts eval

demo:
	@$(PYTHON) -c "from pathlib import Path; import sys; missing = [str(path) for path in (Path('core/omnigrid/cli.py'), Path('data/synthetic/complaints.geojson')) if not path.is_file()]; print('Error: demo requires the Omni-Grid CLI and synthetic complaint input; missing: ' + ', '.join(missing) if missing else ''); sys.exit(bool(missing))"
	$(PYTHON) -m omnigrid.cli triage data/synthetic/complaints.geojson

export-demo:
	$(PYTHON) scripts/export_demo_report.py

eval:
	$(PYTHON) -m eval.run_eval --output eval/results.md
