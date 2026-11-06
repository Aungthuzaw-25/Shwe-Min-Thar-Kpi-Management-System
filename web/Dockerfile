# Stage 1: build Tailwind + Phase 5b asset bundles. The web tier never
# serves static files (the proxy routes /static/ to the API), so only the
# manifest is carried into the final image — but it must be built from the
# SAME pinned inputs as the API image, or the two images' hashes would
# disagree. app/ is copied only for Tailwind's content scan (templates +
# app/**/*.py) and is dropped with this stage.
FROM node:26.12.1-alpine AS asset-builder

WORKDIR /app
COPY tailwind.config.js ./
COPY static/css/ ./static/css/
COPY templates/ ./templates/
COPY app/ ./app/
COPY tools/build_asset_bundles.mjs ./tools/
RUN apk add --no-cache libc6-compat && \
    npm install --global --ignore-scripts tailwindcss@3.4.17 esbuild@0.28.2 && \
    tailwindcss -i ./static/css/input.css -o ./static/css/output.css --minify && \
    node tools/build_asset_bundles.mjs

FROM node:26.12.1-alpine

WORKDIR /app
COPY web/package.json web/package-lock.json ./web/
RUN npm ci --ignore-scripts --omit=dev --prefix ./web
COPY web/src ./web/src
COPY templates ./templates
COPY static/css/bundles.json ./static/css/bundles.json
COPY --from=asset-builder /app/static/asset-manifest.json ./static/asset-manifest.json

WORKDIR /app/web
ENV PORT=3000
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/auth/login').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]
USER node
CMD ["node", "src/server.js"]
USER node
