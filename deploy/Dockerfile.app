# UNO (FastAPI) + pašto procesas (tas pats vaizdas). Kontekstas: repo šaknis (compose: context ..).
# Pinned bazė: tikslus tagas. Skaitmeninę santrauką (digest) galima prisegti: docker buildx imagetools inspect python:3.12.8-slim-bookworm
ARG PYTHON_IMAGE=python:3.12.8-slim-bookworm

FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
COPY requirements.txt /tmp/requirements.txt
# Gamybai testų priklausomybių nereikia
RUN grep -v -i -E '^(pytest|pytest-playwright)==' /tmp/requirements.txt > /tmp/requirements-prod.txt \
    && /venv/bin/pip install -r /tmp/requirements-prod.txt

FROM ${PYTHON_IMAGE}
ARG UID=10001
# DejaVu: lietuviški diakritikai PDF (core/invoices.py ieško /usr/share/fonts/truetype/dejavu)
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid ${UID} --create-home --home-dir /home/uno --shell /usr/sbin/nologin uno \
    && mkdir -p /data /srv/app \
    && chown uno:uno /data /srv/app
COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UNO_DB=/data/uno.db
# Chromium + sisteminės bibliotekos sąskaitų nuorodoms (core/link_invoices.py). Diegiama kaip root į /opt/pw-browsers,
# skaitoma naudotojui uno; versija sutampa su playwright==... iš requirements.txt.
RUN python -m playwright install --with-deps chromium \
    && chmod -R a+rX /opt/pw-browsers \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/app
COPY --chown=uno:uno app ./app
COPY --chown=uno:uno core ./core
COPY --chown=uno:uno data ./data
COPY --chown=uno:uno tools ./tools
# data/out, data/generated, data/hr.db: rašomi vykdant (tmpfs nereikia, bet katalogas turi būti rašomas)
RUN mkdir -p data/out data/generated && chown -R uno:uno data
USER uno
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]
# Vienas procesas: modulių rezultatai laikomi atmintyje (app/main.py RESULTS). Už Caddy: proxy antraštės patikimos,
# nes portas 8000 nepublikuojamas. --no-access-log: URL turi /hr/login/ ir /hr/sign/ žetonus, jų žurnale būti negali.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*", "--no-access-log"]
