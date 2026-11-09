FROM ghcr.io/google/cadvisor:0.60.6@sha256:b8e7d1093144fd088f425ff003d75a4aa405de075db78dae3bc563730b1bd07a
ARG CADVISOR_RUNTIME_USER=0
USER root
RUN apk upgrade --no-cache musl musl-utils zlib
USER ${CADVISOR_RUNTIME_USER}
