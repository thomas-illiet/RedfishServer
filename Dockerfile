FROM ghcr.io/astral-sh/uv:0.8.11@sha256:8101ad825250a114e7bef89eefaa73c31e34e10ffbe5aff01562740bac97553c AS uv
FROM python:3.14.10-alpine3.24@sha256:a18b20ad69e74df6cf6d2e1b1a260d7a1ecf5380fd83b310f83ba88c461cf894 AS base
FROM base AS dependencies
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
RUN ["/usr/local/bin/uv", "sync", "--frozen", "--no-dev", "--no-group", "audit", "--no-install-project"]

FROM base AS minimal
# Keep installed-package metadata and CA roots; remove actual tooling, not scan evidence.
RUN apk add --no-network ca-certificates-bundle alpine-baselayout-data \
    && apk del --no-network --no-scripts apk-tools alpine-baselayout busybox \
       busybox-binsh musl-utils scanelf ssl_client ca-certificates
RUN ["python", "-c", "from pathlib import Path; import shutil; roots = [Path('/usr/local/lib/python3.14/site-packages'), Path('/usr/local/bin')]; [shutil.rmtree(p) if p.is_dir() else p.unlink() for root in roots for p in root.glob('pip*')]; shutil.rmtree('/usr/local/lib/python3.14/ensurepip')"]

FROM scratch
COPY --from=minimal / /
LABEL org.opencontainers.image.title="RedfishServer" \
      org.opencontainers.image.version="0.2.0" \
      org.opencontainers.image.description="Stateful Redfish simulator for internal integration testing"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:/usr/local/bin:/usr/bin:/bin"
WORKDIR /app
COPY --from=dependencies /app/.venv /app/.venv
COPY redfish_mock /app/redfish_mock
COPY mockups /app/mockups
COPY README.md /app/README.md
COPY docs /app/docs
USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=2)"]
ENTRYPOINT ["/app/.venv/bin/python", "-m", "redfish_mock"]
CMD ["--vendor", "dell", "--host", "0.0.0.0", "--port", "8000"]
