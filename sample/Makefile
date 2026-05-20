IMAGE ?= cocoindex-code:local-layered
COMPOSE ?= docker compose -f docker-compose.yml
CCC_VARIANT ?= slim

.PHONY: build build-local build-pypi up restart ps logs down reset install-ccc-wrapper

build: build-local

build-local:
	docker build \
		-t "$(IMAGE)" \
		-f ../docker/Dockerfile \
		--build-arg CCC_VARIANT="$(CCC_VARIANT)" \
		--build-arg CCC_INSTALL_SPEC=/ccc-src \
		..

build-pypi:
	docker build \
		-t "$(IMAGE)" \
		-f ../docker/Dockerfile \
		--build-arg CCC_VARIANT="$(CCC_VARIANT)" \
		..

ps:
	docker ps --filter 'name=cocoindex-code-local-daemon'

up:
	COCOINDEX_CODE_IMAGE="$(IMAGE)" $(COMPOSE) up -d

restart:
	COCOINDEX_CODE_IMAGE="$(IMAGE)" $(COMPOSE) down
	COCOINDEX_CODE_IMAGE="$(IMAGE)" $(COMPOSE) up -d

logs:
	$(COMPOSE) logs -f cocoindex-code-daemon

down:
	$(COMPOSE) down || docker rm -f "$${COCOINDEX_CODE_DAEMON_CONTAINER:-cocoindex-code-local-daemon}" || true

reset: down
	docker volume rm \
		"$${COCOINDEX_CODE_STATE_VOLUME:-cocoindex-code-local-state}" \
		"$${COCOINDEX_CODE_RUNTIME_VOLUME:-cocoindex-code-local-runtime}" \
		2>/dev/null || true
	docker network rm "$${COCOINDEX_CODE_DOCKER_NETWORK:-cocoindex-code-local}" 2>/dev/null || true
	rm -rf data

install-ccc-wrapper:
	mkdir -p "$(HOME)/.local/bin"
	cp "bin/ccc" "$(HOME)/.local/bin/ccc"
	chmod +x "$(HOME)/.local/bin/ccc"
