# DynDNS
#
ARG DOCKER_REGISTRY="docker.io/library"
FROM ${DOCKER_REGISTRY}/python:3.10-alpine3.16 as base
ENV REDIS_HOST=""

WORKDIR /app

ADD src /app
ADD scripts/install-polite-lib.sh /install-polite-lib.sh
RUN pip install --upgrade pip
RUN sh /install-polite-lib.sh && pip install -r /app/requirements.txt

CMD /app/dyndns.py
