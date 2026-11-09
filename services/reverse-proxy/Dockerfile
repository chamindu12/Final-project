FROM ghcr.io/nginx/nginx-unprivileged:1.30.6-alpine3.24@sha256:44e74ca483234093081138b6d7b6f561d55557756a2deefc3a5056148defeb99
ARG NGINX_RUNTIME_USER=101
USER root
RUN apk upgrade --no-cache libexpat
USER ${NGINX_RUNTIME_USER}
