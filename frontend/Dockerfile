# Frontend, built as a static bundle and served by nginx. In this split
# k8s setup the backend (../Dockerfile) is a separate deployment/Service —
# nginx.conf proxies /api/* to it so the browser still sees one origin,
# the same guarantee app.py gives for free when it serves frontend/dist
# itself (see CLAUDE.md: "mesma origem sempre").
FROM node:22-slim AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
RUN npm run build

FROM nginx:1.27-alpine AS runtime

# nginx's own docker-entrypoint envsubst's *.template files in
# /etc/nginx/templates/ into /etc/nginx/conf.d/ on container start, using
# whatever env vars are set at runtime — that's how BACKEND_HOST/PORT
# below get overridden per k8s environment without a rebuild.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

ENV BACKEND_HOST=fusion-service-backend \
    BACKEND_PORT=5050

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/ >/dev/null || exit 1
