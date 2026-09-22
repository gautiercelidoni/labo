"""Service CIQ : saisie évaluée, commentaires, rejets, annulation, limites, historique figé."""
from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models.actions import CorrectiveAction
from app.models.ciq import CIQResult, CIQRun, ControlLimitSet
from app.repositories.base import repo
from app.services import ciq_service
from app.services.ciq_service import CIQError, EntryInput
from tests import factories
from tests.factories import run_at, value_for


@pytest.fixture
def ctx(app, world):
    with factories.acting_as(app, world.emails["quality"], world.lab_a) as user:
        yield user


def entries(setup, *zs, comment=None):
    return [EntryInput(setup.levels[i], setup.lots[i], value_for(setup.limits[i], z), comment)
            for i, z in enumerate(zs) if z is not None]


def test_accepted_run_is_stored_with_z_and_limits(ctx):
    setup = factories.make_ciq_setup()
    run, previews = ciq_service.create_run(setup.parameter, run_at(1), entries(setup, 0.5, -1.0))
    assert run.status == "accepted"
    results = repo(CIQResult).all(CIQResult.run_id == run.id, order_by=CIQResult.created_at)
    assert [r.z_score for r in results] == [Decimal("0.5"), Decimal("-1.0")]
    assert results[0].deviation == Decimal("1.0")  # écart à la cible : 0,5 × s(2)
    assert results[0].limit_set_id == setup.limits[0].id


def test_comment_required_on_warning_and_reject(ctx):
    setup = factories.make_ciq_setup()
    run, previews = ciq_service.create_run(setup.parameter, run_at(1), entries(setup, 2.5, 0.1))
    assert run is None
    assert any("Commentaire obligatoire" in e for p in previews for e in p.errors)
    db.session.rollback()
    run, _ = ciq_service.create_run(setup.parameter, run_at(1), entries(setup, 2.5, 0.1, comment="contrôle repassé"))
    assert run.status == "accepted"
    assert repo(CIQResult).first(CIQResult.run_id == run.id, CIQResult.status == "warning") is not None


def test_comment_requirement_is_configurable(ctx):
    setup = factories.make_ciq_setup()
    ciq_service.update_rule_config(setup.parameter, mode="westgard", rules={}, comment_required_on=["reject"],
                                   min_reference_points=20, chain_lots=False)
    run, _ = ciq_service.create_run(setup.parameter, run_at(1), entries(setup, 2.5, 0.1))
    assert run is not None


def test_reject_requires_justification_and_corrective_action(ctx, world):
    setup = factories.make_ciq_setup()
    run, _ = ciq_service.create_run(setup.parameter, run_at(1), entries(setup, 3.5, 0.1, comment="rejet"))
    assert run.status == "rejected"
    with pytest.raises(CIQError):
        ciq_service.justify_run(run, justification="", action_description="x", responsible_id=None, due_on=None)
    with pytest.raises(CIQError):
        ciq_service.justify_run(run, justification="analyse", action_description="", responsible_id=None, due_on=None)
    action = ciq_service.justify_run(run, justification="Réactif périmé", action_description="Changer le réactif",
                                     responsible_id=world.tech_id, due_on=None)
    assert run.status == "justified"
    assert action.source_type == "ciq_run" and action.source_id == run.id
    from app.models.audit import AuditEvent

    assert repo(AuditEvent).first(AuditEvent.action == "ciq.run.justified").reason == "Réactif périmé"


def test_intra_run_rules_are_evaluated_on_whole_series(ctx):
    setup = factories.make_ciq_setup()
    run, previews = ciq_service.create_run(setup.parameter, run_at(1), entries(setup, 2.3, -2.4, comment="R4s"))
    assert run.status == "rejected"
    for preview in previews:
        assert "R-4s" in preview.evaluation.triggered


def test_rules_use_history_across_runs(ctx):
    setup = factories.make_ciq_setup(levels=1)
    ciq_service.create_run(setup.parameter, run_at(3), entries(setup, 2.2, comment="alerte"))
    run, previews = ciq_service.create_run(setup.parameter, run_at(2), entries(setup, 2.4, comment="alerte"))
    assert "2-2s" in previews[0].evaluation.triggered
    assert run.status == "rejected"


def test_void_and_reentry(ctx):
    setup = factories.make_ciq_setup()
    run, _ = ciq_service.create_run(setup.parameter, run_at(1), entries(setup, 3.6, 0.2, comment="saisie"))
    assert run.status == "rejected"
    wrong = repo(CIQResult).first(CIQResult.run_id == run.id, CIQResult.level_id == setup.levels[0].id)
    with pytest.raises(CIQError):
        ciq_service.void_result(wrong, "")
    ciq_service.void_result(wrong, "Erreur de frappe")
    assert wrong.voided_at is not None and wrong.value == value_for(setup.limits[0], 3.6)  # jamais modifié
    assert run.status == "accepted"  # le seul résultat valide restant est accepté
    result, preview = ciq_service.add_result(run, EntryInput(setup.levels[0], setup.lots[0],
                                                             value_for(setup.limits[0], 0.3)))
    assert result.status == "accepted"
    assert repo(CIQResult).count(CIQResult.run_id == run.id) == 3
    with pytest.raises(CIQError):
        ciq_service.add_result(run, EntryInput(setup.levels[0], setup.lots[0], Decimal(1)))


def test_voided_result_is_ignored_by_later_evaluations(ctx):
    setup = factories.make_ciq_setup(levels=1)
    run1, _ = ciq_service.create_run(setup.parameter, run_at(3), entries(setup, 2.5, comment="x"))
    ciq_service.void_result(repo(CIQResult).first(CIQResult.run_id == run1.id), "mauvais tube")
    run2, previews = ciq_service.create_run(setup.parameter, run_at(2), entries(setup, 2.5, comment="x"))
    assert "2-2s" not in previews[0].evaluation.triggered


def test_new_limits_never_reevaluate_history(ctx):
    setup = factories.make_ciq_setup(levels=1)
    run, _ = ciq_service.create_run(setup.parameter, run_at(2), entries(setup, 1.0))
    original = repo(CIQResult).first(CIQResult.run_id == run.id)
    with pytest.raises(CIQError):
        ciq_service.set_limits(setup.lots[0], mode="westgard", mean=Decimal(90), sd=Decimal(1), source="lab",
                               reason="")  # motif obligatoire pour remplacer
    new = ciq_service.set_limits(setup.lots[0], mode="westgard", mean=Decimal(90), sd=Decimal(1), source="lab",
                                 reason="Nouvelle valeur cible du fournisseur")
    db.session.refresh(original)
    assert original.limit_set_id == setup.limits[0].id and original.status == "accepted"
    assert original.z_score == Decimal("1.0")
    history = ciq_service.limit_history(setup.lots[0])
    assert len(history) == 2 and history[1].valid_to is not None
    run2, _ = ciq_service.create_run(setup.parameter, run_at(1),
                                     [EntryInput(setup.levels[0], setup.lots[0], Decimal("90.5"))])
    later = repo(CIQResult).first(CIQResult.run_id == run2.id)
    assert later.limit_set_id == new.id


def test_single_active_limit_set_per_lot_enforced_in_db(ctx):
    setup = factories.make_ciq_setup(levels=1)
    from app.models.base import utcnow
    from sqlalchemy.exc import IntegrityError

    db.session.add(ControlLimitSet(lot_id=setup.lots[0].id, mode="westgard", mean=Decimal(1), sd=Decimal(1),
                                   source="lab", valid_from=utcnow()))
    with pytest.raises(IntegrityError):
        db.session.flush()
    db.session.rollback()


def test_shewhart_reference_period_and_insufficient_warning(ctx):
    setup = factories.make_ciq_setup(mode="shewhart", levels=1, name="Nitrates")
    for i, z in enumerate([0.2, -0.4, 0.5, -0.1, 0.3]):
        ciq_service.create_run(setup.parameter, run_at(20 - i), entries(setup, z))
    start, end = run_at(21), run_at(10)
    with pytest.raises(CIQError, match="minimum 20"):
        ciq_service.compute_limits_from_reference(setup.lots[0], mode="shewhart", ref_from=start, ref_to=end,
                                                  reason="initial", min_points=20)
    limits = ciq_service.compute_limits_from_reference(setup.lots[0], mode="shewhart", ref_from=start, ref_to=end,
                                                       reason="initial", min_points=20, accept_insufficient=True)
    assert limits.n_reference == 5 and limits.source == "computed"
    assert limits.mean == Decimal("100.200000")
    with pytest.raises(CIQError, match="motif"):
        ciq_service.compute_limits_from_reference(setup.lots[0], mode="shewhart", ref_from=start, ref_to=end,
                                                  reason=" ", min_points=5)


def test_lot_change_resets_sequences_in_service(ctx):
    setup = factories.make_ciq_setup(levels=1)
    ciq_service.create_run(setup.parameter, run_at(3), entries(setup, 2.2, comment="x"))
    lot2 = ciq_service.save_lot(setup.levels[0], None, lot_number="NOUVEAU", manufacturer=None, expires_on=None,
                                in_use_from=None, in_use_to=None)
    ciq_service.set_limits(lot2, mode="westgard", mean=Decimal(100), sd=Decimal(2), source="supplier", reason=None)
    run, previews = ciq_service.create_run(
        setup.parameter, run_at(2), [EntryInput(setup.levels[0], lot2, Decimal("104.6"), "x")])
    assert "2-2s" not in previews[0].evaluation.triggered


def test_missing_limits_block_entry(ctx):
    setup = factories.make_ciq_setup(levels=1)
    lot2 = ciq_service.save_lot(setup.levels[0], None, lot_number="SANS-LIMITES", manufacturer=None, expires_on=None,
                                in_use_from=None, in_use_to=None)
    run, previews = ciq_service.create_run(setup.parameter, run_at(1),
                                           [EntryInput(setup.levels[0], lot2, Decimal(100))])
    assert run is None and "Aucune limite" in previews[0].errors[0]


def test_http_entry_flow(app, client, world):
    from tests.conftest import login

    with factories.acting_as(app, world.emails["quality"], world.lab_a):
        setup = factories.make_ciq_setup()
        ids = [lvl.id for lvl in setup.levels], setup.parameter.id
        db.session.commit()
    level_ids, param_id = ids
    login(client, world.emails["tech"])
    form = {"parametre": str(param_id), "run_at": "2026-05-04T09:15", "operator_id": str(world.tech_id),
            f"value_{level_ids[0]}": "101,5", f"value_{level_ids[1]}": "199.2"}
    response = client.post("/ciq/saisie", data=form)
    assert response.status_code == 302 and "/ciq/series/" in response.headers["Location"]
    page = client.get(response.headers["Location"]).data.decode()
    assert "Acceptée" in page and "101,50" in page
    # Saisie en alerte sans commentaire : refusée, formulaire réaffiché avec les règles déclenchées.
    form[f"value_{level_ids[0]}"] = "105"
    response = client.post("/ciq/saisie", data=form)
    assert response.status_code == 200 and "Commentaire obligatoire" in response.data.decode()
    with app.app_context():
        from app.security.tenancy import tenant_context

        with tenant_context(world.lab_a):
            assert repo(CIQRun).count() == 1


def test_chart_json(app, client, world):
    from tests.conftest import login

    with factories.acting_as(app, world.emails["quality"], world.lab_a):
        setup = factories.make_ciq_setup(levels=1)
        for i, z in enumerate([0.1, 2.5, -0.3]):
            ciq_service.create_run(setup.parameter, run_at(5 - i), entries(setup, z, comment="c"))
        param_id, level_id = setup.parameter.id, setup.levels[0].id
        db.session.commit()
    login(client, world.emails["reader"])
    data = client.get(f"/ciq/parametres/{param_id}/niveaux/{level_id}/graphique.json").json
    assert [p["status"] for p in data["points"]] == ["accepted", "warning", "accepted"]
    assert data["points"][1]["rules"] == ["1-2s"]
    assert data["mode"] == "westgard"


def test_justification_via_http_requires_quality(app, client, world):
    from tests.conftest import login

    with factories.acting_as(app, world.emails["quality"], world.lab_a):
        setup = factories.make_ciq_setup(levels=1)
        run, _ = ciq_service.create_run(setup.parameter, run_at(1), entries(setup, 3.5, comment="rejet"))
        run_id = run.id
        db.session.commit()
    login(client, world.emails["tech"])
    data = {"j-justification": "x", "j-action_description": "y", "j-responsible_id": "", "j-existing_action_id": ""}
    assert client.post(f"/ciq/series/{run_id}/justifier", data=data).status_code == 403
    login(client, world.emails["quality"])
    assert client.post(f"/ciq/series/{run_id}/justifier", data=data).status_code == 302
    with app.app_context():
        from app.security.tenancy import tenant_context

        with tenant_context(world.lab_a):
            assert repo(CIQRun).get(run_id).status == "justified"
            assert repo(CorrectiveAction).count(CorrectiveAction.source_id == run_id) == 1


def test_history_filters(app, client, world):
    from tests.conftest import login

    with factories.acting_as(app, world.emails["quality"], world.lab_a):
        setup = factories.make_ciq_setup(levels=1)
        ciq_service.create_run(setup.parameter, run_at(2), entries(setup, 0.1))
        ciq_service.create_run(setup.parameter, run_at(1), entries(setup, 3.5, comment="rejet ici"))
        db.session.commit()
    login(client, world.emails["reader"])
    page = client.get("/ciq/historique?statut=rejected").data.decode()
    assert "1-3s" in page and page.count("<tr") == 2  # en-tête + 1 ligne
    csv = client.get("/ciq/historique/export.csv?statut=rejected").data.decode("utf-8-sig")
    assert csv.count("\r\n") == 2 and "rejet ici" in csv


def test_run_status_query(ctx):
    setup = factories.make_ciq_setup(levels=1)
    ciq_service.create_run(setup.parameter, run_at(1), entries(setup, 3.2, comment="x"))
    assert db.session.scalar(sa.select(sa.func.count()).select_from(CIQRun).where(CIQRun.status == "rejected")) == 1
