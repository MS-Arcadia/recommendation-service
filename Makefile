# Arcadia recommendation service.
#
#   make install   create the virtualenv and install everything
#   make test      run the checks
#   make lint      ruff check plus format check, then mypy --strict
#   make docker    build the image
#   make run       run locally against the infra stack
#
# This service's own recipes live in the justfile; these targets just shell out to the
# equivalent `just` recipe, so `make test`/`make docker` work the same way here as on every
# other Python service in the platform without maintaining the command twice.

SERVICE := recommendation-service
IMAGE   := arcadia/$(SERVICE)
VERSION ?= local

.DEFAULT_GOAL := help
.PHONY: help install test lint docker run

help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Create the virtualenv and install dependencies
	just install

test: ## Run the full check suite
	just check

lint: ## ruff check plus format check
	just lint

docker: ## Build the image
	docker build --build-arg VERSION=$(VERSION) -t $(IMAGE):$(VERSION) .
	@echo "built $(IMAGE):$(VERSION)"

run: ## Run locally against the infra stack
	just run
