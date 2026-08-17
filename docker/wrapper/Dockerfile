# syntax=docker/dockerfile:1.7
# Hardened DooD wrapper ("lg-wrapper"): runs the ENTIRE LearnGraph compose
# stack against the HOST Docker Engine through a single mounted socket.
#
# BUILD: always via docker/wrapper/run.sh, which builds this image from a
# clean tar context (deterministic excludes). Do NOT `docker compose build`
# this Dockerfile on Docker Desktop: its lazy context fetch has been observed
# to bypass .dockerignore for very large directories (.claude, node_modules),
# silently baking local dev history into the image.
#
# Security model (thin-shell doctrine):
#   - The wrapper grants NOTHING to the inner stack; it only orchestrates.
#   - Non-root uid 65532; the docker-socket GID is injected at runtime via
#     group_add (see docker-compose.wrapper.yml), never baked into the image.
#   - Runtime hardening lives in docker/wrapper/docker-compose.wrapper.yml:
#     network_mode: none, read_only: true, cap_drop: ALL, no-new-privileges,
#     tmpfs /tmp, pids_limit.
#   - No ports are ever published by the wrapper; the inner stack publishes
#     its own ports on the host daemon directly.
#   - No secrets are baked in: sandboxd tokens are generated at first start
#     into mounted named volumes by entrypoint.sh.
#
# Build context MUST be the repo root (see docker-compose.wrapper.yml):
#   docker compose -f docker/wrapper/docker-compose.wrapper.yml build
#
# Full usage + threat model: doc/docker-wrapper.md

FROM alpine:3.20

# docker CLI + compose plugin + tini (PID1 signal forwarding / zombie reaping)
# + openssl (token generation). Keep this list minimal: every extra binary is
# attack surface on a container that holds the host docker socket.
RUN apk add --no-cache \
        docker-cli \
        docker-cli-compose \
        tini \
        openssl \
    && rm -rf /var/cache/apk/*

# Non-root operator (same uid family as the sandbox runners). -H: no home dir;
# HOME/DOCKER_CONFIG point at the mounted writable state volume at runtime.
RUN addgroup -g 65532 lgops \
    && adduser -D -H -s /bin/sh -u 65532 -G lgops lgops

# Bundle the full project source. This is the build context the compose CLI
# streams to the host daemon when building inner images (app/preview/
# egress-proxy/sandboxd). .dockerignore keeps secrets/data/vendored dirs out.
WORKDIR /project
COPY . /project

COPY docker/wrapper/entrypoint.sh /usr/local/bin/lg-wrapper-entrypoint
RUN chmod 0555 /usr/local/bin/lg-wrapper-entrypoint

ENV HOME=/run/wrapper-state \
    DOCKER_CONFIG=/run/wrapper-state/.docker \
    LG_COMPOSE_PROJECT_DIR=/project

USER lgops
ENTRYPOINT ["tini", "-g", "--", "/usr/local/bin/lg-wrapper-entrypoint"]
