FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/crognlie/pste"
LABEL org.opencontainers.image.licenses="MIT"
RUN useradd -m -u 1000 pste
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir ".[gcs]" \
    && mkdir -p /app/data \
    && chown pste:pste /app/data
USER pste
EXPOSE 8000
ENV SQLITE_PATH=/app/data/pste.db
CMD ["pste-server"]
