"""Parcours de base : toutes les pages principales s'affichent pour chaque rôle."""
import pytest

from tests.conftest import login

PAGES = ["/tableau-de-bord", "/ciq/", "/ciq/historique", "/metrologie/", "/metrologie/echeances",
         "/metrologie/calendrier", "/actions-correctives/", "/transmissions/", "/non-conformites/",
         "/notifications/", "/laboratoires"]


@pytest.mark.parametrize("who", ["admin", "quality", "tech", "reader"])
def test_main_pages_render(client, world, who):
    login(client, world.emails[who])
    for page in PAGES:
        response = client.get(page)
        assert response.status_code == 200, (page, response.status_code)


def test_admin_pages(client, world):
    login(client, world.emails["admin"])
    for page in ["/administration/", "/administration/membres", "/administration/equipes",
                 "/administration/categories", "/administration/export-donnees", "/abonnement", "/audit/",
                 "/ciq/saisie", "/ciq/parametres/nouveau", "/metrologie/equipements/nouveau",
                 "/transmissions/nouvelle", "/non-conformites/nouvelle", "/actions-correctives/nouvelle",
                 "/compte/mot-de-passe"]:
        response = client.get(page)
        assert response.status_code == 200, (page, response.status_code)


def test_health(client):
    response = client.get("/sante")
    assert response.status_code == 200
    assert response.json == {"statut": "ok", "base": "ok"}
