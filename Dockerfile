# DynDNS
ARG DOCKER_REGISTRY="docker.io/library"
FROM ${DOCKER_REGISTRY}/python:3.13-alpine AS base
WORKDIR /app
COPY src/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY src/dyndns.py /app/dyndns.py

# docker build --target test . runs the unit tests.
FROM base AS test
COPY requirements-dev.txt /tmp/requirements-dev.txt
COPY src /tmp/src
COPY tests /tmp/tests
RUN pip install --no-cache-dir -r /tmp/requirements-dev.txt && cd /tmp && python -m pytest -q tests

FROM base AS release
USER 65534
CMD ["python3", "/app/dyndns.py"]
