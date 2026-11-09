# Small "workstation" image for the terminal clients (staff-client and isolated-test)
FROM alpine:3.24
RUN apk upgrade --no-cache \
    && apk add --no-cache curl bind-tools iputils
USER nobody
CMD ["sleep", "infinity"]