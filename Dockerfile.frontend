FROM nginx:1.27-alpine

COPY frontend /usr/share/nginx/html
COPY docker/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY docker/frontend/docker-entrypoint.sh /docker-entrypoint.d/40-smart-inventory-config.sh

RUN chmod +x /docker-entrypoint.d/40-smart-inventory-config.sh

EXPOSE 5500

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1:5500/login >/dev/null || exit 1
