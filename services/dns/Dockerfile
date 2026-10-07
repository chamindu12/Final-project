FROM alpine:3.20
RUN apk add --no-cache dnsmasq bind-tools
COPY dnsmasq.conf /etc/dnsmasq.conf
USER nobody
EXPOSE 5353/udp
CMD ["dnsmasq", "--keep-in-foreground", "--conf-file=/etc/dnsmasq.conf"]
