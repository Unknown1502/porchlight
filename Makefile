# Thin delegation to tasks.py, which is the single definition of every target.
#
# `make` is not present on a stock Windows install, so the README's commands
# failed outright for a judge on Windows. tasks.py is the portable entry point:
#
#     python tasks.py test          # works everywhere
#     make test                     # works where make exists
#
# Keeping the recipes in one place means the two cannot drift.

.PHONY: install corpus run demo eval seeds test injection lint docs check deploy clean

PY ?= python

install corpus run demo eval seeds test injection lint docs check deploy clean:
	$(PY) tasks.py $@
