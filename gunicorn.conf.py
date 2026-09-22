"""Configuration Gunicorn (production)."""
import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
# 2 à 4 workers suffisent largement pour quelques laboratoires de 2 à 15 utilisateurs.
workers = int(os.environ.get("GUNICORN_WORKERS", min(4, max(2, multiprocessing.cpu_count()))))
threads = int(os.environ.get("GUNICORN_THREADS", 2))
worker_class = "gthread"
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 60))  # génération PDF incluse
graceful_timeout = 30
keepalive = 5
max_requests = 1000
max_requests_jitter = 100
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info").lower()
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
