IMAGE ?= cocoindex-code:local-layered
COMPOSE ?= docker compose -f docker/docker-compose.yml
CCC_VARIANT ?= slim
CCC_WRAPPER ?= bin/ccc
DAEMON_CONTAINER ?= cocoindex-code-local-daemon
STATE_VOLUME ?= cocoindex-code-local-state
RUNTIME_VOLUME ?= cocoindex-code-local-runtime

.PHONY: build build-local build-pypi up restart ps logs down reset install-ccc-wrapper

build: build-local

build-local:
	docker build \
		-t "$(IMAGE)" \
		-f docker/Dockerfile \
		--build-arg CCC_VARIANT="$(CCC_VARIANT)" \
		--build-arg CCC_INSTALL_SPEC=/ccc-src \
		.

build-pypi:
	docker build \
		-t "$(IMAGE)" \
		-f docker/Dockerfile \
		--build-arg CCC_VARIANT="$(CCC_VARIANT)" \
		.

ps:
	docker ps --filter 'name=cocoindex-code-local-daemon'

up:
	docker volume inspect "$(STATE_VOLUME)" >/dev/null 2>&1 || docker volume create "$(STATE_VOLUME)" >/dev/null
	docker volume inspect "$(RUNTIME_VOLUME)" >/dev/null 2>&1 || docker volume create "$(RUNTIME_VOLUME)" >/dev/null
	@if docker inspect "$(DAEMON_CONTAINER)" >/dev/null 2>&1 && \
		[ -z "$$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$(DAEMON_CONTAINER)" 2>/dev/null)" ]; then \
		echo "Removing non-Compose daemon container $(DAEMON_CONTAINER) before compose up"; \
		docker rm -f "$(DAEMON_CONTAINER)" >/dev/null; \
	fi
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

install-ccc-wrapper:
	mkdir -p "$(HOME)/.local/bin"
	cp "$(CCC_WRAPPER)" "$(HOME)/.local/bin/ccc"
	chmod +x "$(HOME)/.local/bin/ccc"
