FROM python:3.12-slim
# Zero-dependency server; only the lookup sources need TLS roots.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY lookup.py server.py tool.json ./
RUN useradd -r -u 10001 kcap
USER kcap
ENV KCAP_PORT=8080 KCAP_TRUST_PROXY=1 PYTHONUNBUFFERED=1
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=4)"
CMD ["python3", "server.py"]
