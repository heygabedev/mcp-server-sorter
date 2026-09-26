FROM node:24-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit
COPY web/ ./
COPY src/mcp_sorter/data/catalog.json /src/mcp_sorter/data/catalog.json
RUN npm run build

FROM python:3.13-slim-bookworm@sha256:2325bb286ec344af3e5898cc224b5844e2707ac6e26b1632516fd3edc84a5e26 AS builder
ARG APP_VERSION=0.1.0
WORKDIR /build
COPY . .
COPY --from=web /web/dist ./web/dist
RUN pip install --no-cache-dir uv==0.5.9 \
    && uv sync --frozen --no-editable \
    && uv run --no-sync python scripts/build_release.py --version "$APP_VERSION" --skip-web \
    && uv export --no-dev --no-emit-project --no-hashes --format requirements-txt --output-file /requirements.txt

FROM python:3.13-slim-bookworm@sha256:2325bb286ec344af3e5898cc224b5844e2707ac6e26b1632516fd3edc84a5e26
ARG APP_VERSION=0.1.0
LABEL org.opencontainers.image.source="https://github.com/heygabedev/mcp-server-sorter"
LABEL org.opencontainers.image.version="${APP_VERSION}"
COPY --from=builder /build/dist/*.whl /wheels/
COPY --from=builder /requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt /wheels/*.whl \
    && useradd --uid 10001 --create-home sorter \
    && mkdir /data && chown sorter:sorter /data
USER 10001
WORKDIR /data
ENV SORTER_DATA_DIR=/data SORTER_MODE=demo PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)"
CMD ["mcp-sorter", "serve", "--host", "0.0.0.0"]
