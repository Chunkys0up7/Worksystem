.PHONY: install demo attacks test validate conformance all

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

conformance:
	python -m harness conformance recorded --show-run

all: validate test attacks conformance demo
