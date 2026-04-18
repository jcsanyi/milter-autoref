FROM python:3.12-slim AS builder

ARG SETUPTOOLS_SCM_PRETEND_VERSION

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc python3-dev libmilter-dev && \
    rm -rf /var/lib/apt/lists/*

COPY . /src
RUN SETUPTOOLS_SCM_PRETEND_VERSION="${SETUPTOOLS_SCM_PRETEND_VERSION}" \
    pip install --no-cache-dir /src

FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends libmilter1.0.1 && \
    rm -rf /var/lib/apt/lists/* && \
    useradd --system --no-create-home milter

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/milter-autoref /usr/local/bin/milter-autoref

USER milter

ENV AUTOREF_SOCKET=inet:8890@0.0.0.0

ENTRYPOINT ["milter-autoref"]
