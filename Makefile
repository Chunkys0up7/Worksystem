.PHONY: install ui journey demo attacks test validate conformance all

install:
	pip install pyyaml cryptography pytest hypothesis

ui:
	python -m harness ui

journey:
	python -m harness journey

demo:
	python -m harness demo

attacks:
	python -m harness attacks

validate:
	python -m harness validate

test:
	python -m pytest -q

conformance:
	python -m harness conformance loop --show-run

all: validate test attacks conformance demo journey
