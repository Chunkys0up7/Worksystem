.PHONY: install demo attacks test validate all

install:
	pip install pyyaml cryptography pytest hypothesis

demo:
	python -m harness demo

attacks:
	python -m harness attacks

validate:
	python -m harness validate

test:
	python -m pytest -q

all: validate test attacks demo
