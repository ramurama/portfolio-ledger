# Shortcuts for local development (expects ./venv from setup.sh).
PYTHON ?= ./venv/bin/python
PIP ?= ./venv/bin/pip

.PHONY: help process reports full-report cost-basis install-editable run

help:
	@echo "Targets:"
	@echo "  make process       -> $(PYTHON) -m app.main process"
	@echo "  make reports       -> generate-reports (interactive)"
	@echo "  make full-report   -> combined PDF + --apply-isin-ignore; optional cash (confirm, then per folder)"
	@echo "  make cost-basis    -> generate-cost-basis"
	@echo "  make run ARGS='…'  -> $(PYTHON) -m app.main <ARGS> (quote the whole CLI tail)"
	@echo "  make install-editable -> pip install -e . (pl, portfolio-ledger)"

process:
	$(PYTHON) -m app.main process

reports:
	$(PYTHON) -m app.main generate-reports

full-report:
	$(PYTHON) -m app.main generate-reports \
		--reports combined \
		--format pdf \
		--apply-isin-ignore

cost-basis:
	$(PYTHON) -m app.main generate-cost-basis

# Pass arbitrary subcommand + flags, e.g.:
#   make run ARGS='generate-reports --reports combined --format pdf --cash ramu:26490 --cash rakshana:7192 --apply-isin-ignore'
run:
	$(PYTHON) -m app.main $(ARGS)

install-editable:
	$(PIP) install -e .
