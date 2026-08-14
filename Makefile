.PHONY: install test lint typecheck demo coevolve clean

install:
	python -m pip install -e '.[dev]'

test:
	pytest

lint:
	ruff check .

typecheck:
	mypy src/post_training_rsi

demo:
	python -m post_training_rsi --workspace artifacts/demo demo

coevolve:
	python -m post_training_rsi --workspace artifacts/coevolution coevolve

clean:
	rm -rf artifacts .pytest_cache .ruff_cache .mypy_cache
