# LearnGraph sandbox runner: Python + Node, non-root, no default network needs.
# Base digest is pinned for reproducibility; Bootstrap builds and re-pins the result.
FROM python:3.12-alpine@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df

RUN apk add --no-cache nodejs npm \
    && addgroup -g 65532 learngraph \
    && adduser -D -H -u 65532 -G learngraph learngraph

COPY --chown=65532:65532 runner.py /opt/learngraph/runner.py
RUN chmod 0555 /opt/learngraph/runner.py \
    && python --version \
    && node --version

USER 65532:65532
WORKDIR /workspace
ENTRYPOINT []
CMD ["sleep", "infinity"]
