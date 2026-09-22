"""Pièces jointes : validation, stockage (local ou S3) et contrôle d'accès au téléchargement."""
from __future__ import annotations

import hashlib
import io
import os
import re
import uuid
import zipfile
from dataclasses import dataclass
from typing import Protocol

import magic
from flask import current_app
from werkzeug.datastructures import FileStorage as UploadedFile

from app.extensions import db
from app.models.files import Attachment
from app.security.tenancy import current_tenant_id
from app.services import audit_service

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ALLOWED_TYPES = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    DOCX: ".docx",
    XLSX: ".xlsx",
    "text/csv": ".csv",
}
IMAGE_TYPES = {"image/png", "image/jpeg"}


class UploadError(ValueError):
    pass


class Storage(Protocol):
    def save(self, key: str, data: bytes, content_type: str) -> None: ...
    def read(self, key: str) -> bytes: ...
    def presigned_url(self, key: str, filename: str, content_type: str) -> str | None: ...


class LocalStorage:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)

    def _path(self, key: str) -> str:
        # La clé est générée par l'application ({tenant_uuid}/{uuid}) ; on refuse tout le reste.
        if not re.fullmatch(r"[0-9a-f-]{36}/[0-9a-f-]{36}", key):
            raise UploadError("Clé de stockage invalide.")
        path = os.path.abspath(os.path.join(self.root, key))
        if not path.startswith(self.root + os.sep):
            raise UploadError("Chemin de stockage invalide.")
        return path

    def save(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        os.makedirs(os.path.dirname(path), mode=0o750, exist_ok=True)
        with open(path, "xb") as fh:
            fh.write(data)

    def read(self, key: str) -> bytes:
        with open(self._path(key), "rb") as fh:
            return fh.read()

    def presigned_url(self, key: str, filename: str, content_type: str) -> str | None:
        return None


class S3Storage:
    def __init__(self, cfg):
        import boto3

        self.bucket = cfg["S3_BUCKET"]
        self.expires = cfg["S3_PRESIGN_SECONDS"]
        self.client = boto3.client(
            "s3",
            endpoint_url=cfg["S3_ENDPOINT_URL"] or None,
            region_name=cfg["S3_REGION"],
            aws_access_key_id=cfg["S3_ACCESS_KEY_ID"] or None,
            aws_secret_access_key=cfg["S3_SECRET_ACCESS_KEY"] or None,
        )

    def save(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def read(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def presigned_url(self, key: str, filename: str, content_type: str) -> str | None:
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
                "ResponseContentType": content_type,
            },
            ExpiresIn=self.expires,
        )


def get_storage() -> Storage:
    cfg = current_app.config
    if cfg["STORAGE_BACKEND"] == "s3":
        return S3Storage(cfg)
    return LocalStorage(cfg["UPLOAD_DIR"])


def safe_filename(name: str) -> str:
    """Nom d'affichage uniquement (jamais utilisé comme chemin)."""
    name = os.path.basename(name.replace("\\", "/")).strip()
    name = re.sub(r"[^\w.\- ()À-ÿ]", "_", name)[:200]
    return name or "fichier"


def detect_type(data: bytes, filename: str) -> str:
    """Type réel détecté sur le contenu (octets magiques), l'extension ne sert qu'à départager."""
    detected = magic.from_buffer(data[:8192], mime=True)
    ext = os.path.splitext(filename.lower())[1]
    if detected in ("application/zip", DOCX, XLSX, "application/octet-stream") and data[:2] == b"PK":
        try:
            names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        except zipfile.BadZipFile:
            raise UploadError("Archive bureautique invalide.")
        if "[Content_Types].xml" in names:
            if any(n.startswith("word/") for n in names):
                return DOCX
            if any(n.startswith("xl/") for n in names):
                return XLSX
        raise UploadError("Type de fichier non autorisé.")
    if detected in ("text/plain", "text/csv", "application/csv") and ext == ".csv":
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                data.decode("cp1252")
            except UnicodeDecodeError:
                raise UploadError("Fichier CSV illisible.")
        return "text/csv"
    return detected


def store_upload(upload: UploadedFile, owner_type: str, owner_id: uuid.UUID) -> Attachment:
    max_bytes = current_app.config["MAX_UPLOAD_MB"] * 1024 * 1024
    data = upload.stream.read(max_bytes + 1)
    if not data:
        raise UploadError("Le fichier est vide.")
    if len(data) > max_bytes:
        raise UploadError(f"Le fichier dépasse {current_app.config['MAX_UPLOAD_MB']} Mo.")
    original = safe_filename(upload.filename or "fichier")
    content_type = detect_type(data, original)
    if content_type not in ALLOWED_TYPES:
        raise UploadError("Type de fichier non autorisé (PDF, PNG, JPEG, DOCX, XLSX, CSV uniquement).")
    tenant_id = current_tenant_id()
    key = f"{tenant_id}/{uuid.uuid4()}"
    get_storage().save(key, data, content_type)
    attachment = Attachment(
        tenant_id=tenant_id,
        storage_key=key,
        original_name=original,
        content_type=content_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        owner_type=owner_type,
        owner_id=owner_id,
    )
    from flask_login import current_user

    if current_user and current_user.is_authenticated:
        attachment.uploaded_by_id = current_user.id
    db.session.add(attachment)
    db.session.flush()
    audit_service.record(
        "file.uploaded",
        attachment,
        after={"name": original, "owner_type": owner_type, "owner_id": str(owner_id), "sha256": attachment.sha256},
    )
    return attachment


@dataclass
class Download:
    attachment: Attachment
    data: bytes | None
    redirect_url: str | None
