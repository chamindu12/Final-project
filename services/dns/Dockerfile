FROM alpine:3.24
RUN apk upgrade --no-cache \
    && apk add --no-cache dnsmasq bind-tools
COPY dnsmasq.conf /etc/dnsmasq.conf
USER nobody
EXPOSE 5353/udp
CMD ["dnsmasq", "--keep-in-foreground", "--conf-file=/etc/dnsmasq.conf"]
