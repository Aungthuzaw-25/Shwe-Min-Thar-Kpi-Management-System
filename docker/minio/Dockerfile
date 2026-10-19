ARG MINIO_RELEASE=RELEASE.2025-09-07T16-13-09Z

FROM golang:1.24-alpine AS build

ARG TARGETARCH
ARG MINIO_RELEASE

RUN apk add --no-cache git

WORKDIR /src
RUN git clone --depth 1 --branch "${MINIO_RELEASE}" https://github.com/minio/minio.git .

RUN mkdir -p /out && \
    CGO_ENABLED=0 GOOS=linux GOARCH="${TARGETARCH}" \
    go build -tags kqueue -trimpath \
      --ldflags "$(MINIO_RELEASE=RELEASE go run buildscripts/gen-ldflags.go)" \
      -o /out/minio .

FROM alpine:3.22

RUN apk add --no-cache ca-certificates curl

COPY --from=build /out/minio /usr/bin/minio
COPY --from=build /src/dockerscripts/docker-entrypoint.sh /usr/bin/docker-entrypoint.sh

EXPOSE 9000
VOLUME ["/data"]

ENTRYPOINT ["/usr/bin/docker-entrypoint.sh"]
CMD ["minio"]
