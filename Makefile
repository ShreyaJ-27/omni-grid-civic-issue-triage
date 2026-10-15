.PHONY: install test lint demo export-demo

PYTHON ?= python

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check core tests

demo:
	@$(PYTHON) -c "from pathlib import Path; import sys; missing = [str(path) for path in (Path('core/omnigrid/cli.py'), Path('data/synthetic/complaints.geojson')) if not path.is_file()]; print('Error: demo requires the Omni-Grid CLI and synthetic complaint input; missing: ' + ', '.join(missing) if missing else ''); sys.exit(bool(missing))"
	$(PYTHON) -m omnigrid.cli triage data/synthetic/complaints.geojson

export-demo:
	@$(PYTHON) -c "from pathlib import Path; import sys; missing = [str(path) for path in (Path('core/omnigrid/cli.py'), Path('data/synthetic/complaints.geojson')) if not path.is_file()]; print('Error: export-demo requires the real Omni-Grid CLI export workflow and synthetic complaint input; missing: ' + ', '.join(missing) if missing else ''); sys.exit(bool(missing))"
	$(PYTHON) -m omnigrid.cli export data/synthetic/complaints.geojson web/demo/report.json
