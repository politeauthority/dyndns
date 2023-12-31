# DynDNS
#
ARG DOCKER_REGISTRY="docker.io/library"
FROM ${DOCKER_REGISTRY}/python:3.10-alpine3.16 as base
ENV REDIS_HOST=""

WORKDIR /app

ADD src /app
RUN pip install --upgrade pip
RUN pip install -r /app/requirements.txt

CMD /app/dyndns.py
