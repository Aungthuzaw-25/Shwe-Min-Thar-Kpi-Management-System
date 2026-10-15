FROM node:20-alpine

WORKDIR /app
COPY web/package.json web/package-lock.json ./web/
RUN npm ci --ignore-scripts --omit=dev --prefix ./web
COPY web/src ./web/src
COPY templates ./templates

WORKDIR /app/web
ENV PORT=3000
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/auth/login').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]
CMD ["node", "src/server.js"]
USER node
