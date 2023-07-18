# DynDNS
#
ARG DOCKER_REGISTRY="docker.io/library"
FROM ${DOCKER_REGISTRY}/python:3.10-alpine3.16 as base
ARG REDIS_HOST=""

WORKDIR /app

# RUN apk add --update --no-cache --virtual .tmp-build-deps \
#     gcc libc-dev linux-headers postgresql-dev \
#     && apk add libffi-dev

# Install Cver
ADD src /app
RUN pip install --upgrade pip
RUN cd /app && pip install -r /app/requirements.txt

CMD /app/dyndns.py
