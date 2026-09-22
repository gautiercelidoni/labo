"""Données de démonstration : génération complète et déclenchement réel de chaque règle."""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models.ciq import CIQResult, CIQRun
from app.models.tenant import Laboratory
from app.security.tenancy import system_context, tenant_context
from tests.conftest import login


@pytest.mark.slow
def test_seed_demo_triggers_every_rule_and_renders(app, client):
    from app.demo import PASSWORD, seed

    with app.app_context():
        credentials = seed()
        with system_context():
            labs = {lab.slug: lab.id for lab in db.session.scalars(sa.select(Laboratory)).all()}
        lab_a = labs["laboratoire-veterinaire-des-alpes"]
        with tenant_context(lab_a):
            triggered = set()
            for rules in db.session.scalars(sa.select(CIQResult.rules_triggered)).all():
                triggered |= {r["rule"] for r in rules}
            assert {"1-2s", "1-3s", "2-2s", "R-4s", "4-1s", "10x",
                    "shewhart_a", "shewhart_b", "shewhart_c", "shewhart_d"} <= triggered
            statuses = set(db.session.scalars(sa.select(CIQRun.status)).all())
            assert {"accepted", "rejected", "justified"} <= statuses
            assert db.session.scalar(sa.select(sa.func.count()).select_from(CIQResult)
                                     .where(CIQResult.voided_at.is_not(None))) == 1
    assert len(credentials["users"]) == 7
    # Le consultant appartient aux deux laboratoires.
    login(client, "consultant@demo.labqualite.fr", lab_a, password=PASSWORD)
    for url in ["/tableau-de-bord", "/ciq/", "/ciq/historique", "/metrologie/", "/metrologie/echeances",
                "/metrologie/calendrier", "/actions-correctives/", "/transmissions/?boite=toutes",
                "/non-conformites/", "/audit/", "/notifications/"]:
        assert client.get(url).status_code == 200, url
    page = client.get("/ciq/").data.decode()
    assert "Glucose" in page and "Nitrates" in page
    client.post(f"/laboratoires/{labs['laboratoire-des-eaux-du-rhone']}/activer")
    page = client.get("/ciq/").data.decode()
    assert "Phosphates" in page and "Glucose" not in page
