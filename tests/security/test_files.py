"""Pièces jointes : liste blanche, détection du type réel, stockage, contrôle d'accès."""
from __future__ import annotations

import io
import os
import zipfile

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models.audit import AuditEvent
from app.models.files import Attachment
from app.repositories.base import repo
from app.security.tenancy import tenant_context
from app.services.file_storage import LocalStorage, UploadError, safe_filename
from tests import factories
from tests.conftest import login

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478"
                    "9c6360000002000154a24f5d0000000049454e44ae426082")


@pytest.fixture
def equipment(app, world):
    with app.app_context(), tenant_context(world.lab_a):
        eq = factories.make_equipment("Pipette", "PIP-1")
        db.session.commit()
        return eq.id


def upload(client, equipment_id, data: bytes, name: str):
    return client.post(f"/metrologie/equipements/{equipment_id}/pieces-jointes",
                       data={"file": (io.BytesIO(data), name)}, content_type="multipart/form-data")


def attachments(app, lab_id):
    with app.app_context(), tenant_context(lab_id):
        return list(repo(Attachment).all())


def test_pdf_upload_is_stored_with_random_key(app, client, world, equipment):
    login(client, world.emails["tech"])
    upload(client, equipment, PDF, "../../etc/certificat étalonnage.pdf")
    [att] = attachments(app, world.lab_a)
    assert att.content_type == "application/pdf"
    assert att.original_name == "certificat étalonnage.pdf"  # nom d'affichage nettoyé
    assert att.storage_key.startswith(f"{world.lab_a}/")
    assert "certificat" not in att.storage_key
    path = os.path.join(app.config["UPLOAD_DIR"], att.storage_key)
    assert os.path.isfile(path)
    assert os.path.realpath(path).startswith(os.path.realpath(app.config["UPLOAD_DIR"]))


def test_type_is_detected_from_content_not_extension(app, client, world, equipment):
    login(client, world.emails["tech"])
    upload(client, equipment, b"#!/bin/sh\nrm -rf /\n", "script.pdf")
    upload(client, equipment, b"<html><script>alert(1)</script></html>", "page.png")
    upload(client, equipment, b"MZ\x90\x00\x03\x00\x00\x00", "outil.xlsx")
    assert attachments(app, world.lab_a) == []


def test_png_and_office_documents_accepted(app, client, world, equipment):
    login(client, world.emails["tech"])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")
    upload(client, equipment, PNG, "photo.png")
    upload(client, equipment, buffer.getvalue(), "rapport.docx")
    upload(client, equipment, "a;b\n1;2\n".encode(), "mesures.csv")
    types = sorted(a.content_type for a in attachments(app, world.lab_a))
    assert types == ["application/vnd.openxmlformats-officedocument.wordprocessingml.document", "image/png",
                     "text/csv"]


def test_size_limit(app, client, world, equipment):
    app.config["MAX_UPLOAD_MB"] = 1
    try:
        login(client, world.emails["tech"])
        upload(client, equipment, PDF + b"0" * (1024 * 1024 + 10), "gros.pdf")
        assert attachments(app, world.lab_a) == []
    finally:
        app.config["MAX_UPLOAD_MB"] = 15


def test_download_is_forced_and_audited(app, client, world, equipment):
    login(client, world.emails["tech"])
    upload(client, equipment, PDF, "certificat.pdf")
    [att] = attachments(app, world.lab_a)
    response = client.get(f"/fichiers/{att.id}")
    assert response.status_code == 200
    assert response.data == PDF
    assert response.headers["Content-Disposition"].startswith("attachment;")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    with app.app_context(), tenant_context(world.lab_a):
        assert repo(AuditEvent).count(AuditEvent.action == "file.downloaded") == 1


def test_file_of_other_lab_is_not_downloadable(app, client, world, equipment):
    login(client, world.emails["tech"])
    upload(client, equipment, PDF, "certificat.pdf")
    [att] = attachments(app, world.lab_a)
    login(client, world.emails["admin_b"])
    assert client.get(f"/fichiers/{att.id}").status_code == 404
    # Même un membre des deux labos ne le voit pas depuis le labo B actif.
    login(client, world.emails["consultant"], world.lab_b)
    assert client.get(f"/fichiers/{att.id}").status_code == 404
    client.post(f"/laboratoires/{world.lab_a}/activer")
    assert client.get(f"/fichiers/{att.id}").status_code == 200


def test_owner_permission_is_checked(app, client, world):
    """Pièce jointe d'une transmission : seuls l'auteur et les destinataires y accèdent."""
    from app.services import transmission_service
    from app.services.file_storage import store_upload
    from werkzeug.datastructures import FileStorage

    with factories.acting_as(app, world.emails["tech"], world.lab_a):
        t = transmission_service.create(title="Privé", body="…", category="general", priority="normal", due_on=None,
                                        user_ids=[world.tech2_id], team_ids=[], attachments=[])
        att = store_upload(FileStorage(io.BytesIO(PDF), "note.pdf"), "transmission", t.id)
        db.session.commit()
        att_id = att.id
    login(client, world.emails["reader"])
    assert client.get(f"/fichiers/{att_id}").status_code == 404
    login(client, world.emails["tech2"])
    assert client.get(f"/fichiers/{att_id}").status_code == 200


def test_local_storage_refuses_path_traversal(tmp_path):
    storage = LocalStorage(str(tmp_path))
    for key in ("../secret", "/etc/passwd", "a/../../b", "00000000-0000-0000-0000-000000000000/../x"):
        with pytest.raises(UploadError):
            storage.read(key)


def test_safe_filename():
    assert safe_filename("..\\..\\windows\\system.ini") == "system.ini"
    assert safe_filename("<script>.pdf") == "_script_.pdf"
    assert safe_filename("") == "fichier"


def test_upload_is_audited(app, client, world, equipment):
    login(client, world.emails["tech"])
    upload(client, equipment, PDF, "c.pdf")
    with app.app_context(), tenant_context(world.lab_a):
        assert db.session.scalar(sa.select(sa.func.count()).select_from(AuditEvent)
                                 .where(AuditEvent.action == "file.uploaded")) == 1
