"""En-têtes HTTP de sécurité appliqués à toutes les réponses."""
from __future__ import annotations

from flask import Flask, Response, current_app

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
        "form-action 'self' https://checkout.stripe.com https://billing.stripe.com",
    ]
)


def apply_security_headers(response: Response) -> Response:
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    if current_app.config.get("HSTS_ENABLED"):
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    if response.mimetype == "text/html":
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def init_headers(app: Flask) -> None:
    app.after_request(apply_security_headers)
