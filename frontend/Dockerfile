# Frontend: node build stage (tsc --noEmit && vite build) -> nginx image.
# This nginx is also the WebSocket load balancer (docker/nginx/default.conf),
# so `docker compose up --scale gateway=3` fans traffic out across replicas.
# Build context is the repository root:  docker build -f frontend/Dockerfile .
FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY docker/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
