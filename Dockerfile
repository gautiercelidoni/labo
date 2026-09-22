# Image applicative LabQualité (Flask + Gunicorn + WeasyPrint)
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FLASK_APP=app:create_app

# Pango/HarfBuzz pour WeasyPrint, libmagic pour la détection du type des pièces jointes,
# polices DejaVu pour les PDF, client PostgreSQL pour les sauvegardes et l'attente de la base.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 libmagic1 \
        fonts-dejavu-core postgresql-client tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system labq && useradd --system --gid labq --home /app --shell /usr/sbin/nologin labq

WORKDIR /app
# Dépendances versionnées lues dans pyproject.toml (couche mise en cache tant qu'il ne change pas).
COPY pyproject.toml ./
RUN pip install --upgrade pip && python -c "import tomllib; d = tomllib.load(open('pyproject.toml', 'rb'))['project']; \
print('\n'.join(d['dependencies'] + d['optional-dependencies']['test']))" > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt
COPY . .
RUN chmod +x /app/deploy/*.sh && mkdir -p /data/uploads && chown -R labq:labq /data /app

USER labq
ENV UPLOAD_DIR=/data/uploads
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/sante', timeout=4).status == 200 else 1)"
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:create_app()"]
