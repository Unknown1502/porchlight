.PHONY: install corpus run demo eval test injection lint deploy clean

install:
	pip install -r requirements-dev.txt && pip install -e .

corpus:
	python corpus/generate.py --out corpus/seed --count 60 --campaigns 1

run:
	PYTHONPATH=src uvicorn porchlight.server:app --reload --port 8080

demo: corpus
	python -m porchlight.cli replay --dir corpus/seed --speed 8

eval:
	python eval/run_eval.py --corpus corpus/seed --labels eval/labels.json

test:
	pytest -q

injection:
	pytest -q tests/injection -s

lint:
	ruff check src corpus eval tests

deploy:
	bash deploy/deploy_runtime.sh

clean:
	rm -rf corpus/seed/*.json eval/out .pytest_cache

docs:
	python3 scripts/gen_prompt_docs.py
