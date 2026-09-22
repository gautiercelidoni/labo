"""Données de démonstration : deux laboratoires, dont un complet, générés via les services métier.

Les résultats CIQ passent par le vrai moteur de règles : chaque règle Westgard et Shewhart est
effectivement déclenchée par des séquences construites à cet effet.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import sqlalchemy as sa
from flask import current_app, g
from flask_login import login_user

from app.extensions import db
from app.models.base import utcnow
from app.models.billing import Subscription
from app.models.ciq import CIQRun, ControlLevel
from app.models.tenant import Laboratory, Membership
from app.models.user import User
from app.security.passwords import hash_password
from app.security.tenancy import tenant_context

PASSWORD = "Demo-Labqualite-2026"
DOMAIN = "demo.labqualite.fr"


@contextmanager
def acting_as(user: User, lab_id):
    """Exécute les services comme si l'utilisateur agissait dans l'application."""
    with current_app.test_request_context("/"):
        with tenant_context(lab_id):
            login_user(user)
            g.membership = db.session.scalar(sa.select(Membership).where(Membership.user_id == user.id))
            g.lab = db.session.scalar(sa.select(Laboratory).where(Laboratory.id == lab_id))
            g.write_allowed = True
            yield


def _user(email: str, name: str) -> User:
    user = User(email=email, full_name=name, password_hash=hash_password(PASSWORD), password_changed_at=utcnow())
    db.session.add(user)
    db.session.flush()
    return user


def _member(user: User, role: str, team_id=None) -> None:
    db.session.add(Membership(user_id=user.id, role=role, team_id=team_id, is_active=True))


def _day(days_ago: int, hour: int = 8) -> datetime:
    d = date.today() - timedelta(days=days_ago)
    return datetime.combine(d, time(hour, 30), tzinfo=timezone.utc)


def _value(mean: Decimal, sd: Decimal, z: float) -> Decimal:
    return mean + Decimal(str(z)) * sd


def _run(parameter, run_at, entries, operator):
    """Enregistre une série ; ajoute un commentaire si le moteur l'exige (alerte ou rejet)."""
    from app.services import ciq_service

    run, previews = ciq_service.create_run(parameter, run_at, entries, operator.id)
    if run is None:
        db.session.rollback()
        for entry, preview in zip(entries, previews):
            if preview.needs_comment and not entry.comment:
                rules = ", ".join(preview.evaluation.triggered)
                entry.comment = f"Démo : règle(s) {rules} déclenchée(s), contrôle repassé."
        run, previews = ciq_service.create_run(parameter, run_at, entries, operator.id)
        if run is None:
            raise RuntimeError(f"Série de démonstration refusée : {[p.errors for p in previews]}")
    return run


def seed() -> dict:
    from app.services import auth_service

    if db.session.scalar(sa.select(Laboratory).where(Laboratory.slug == "laboratoire-veterinaire-des-alpes")):
        raise RuntimeError("Les données de démonstration existent déjà.")

    users = {
        "admin": _user(f"admin@{DOMAIN}", "Claire Admin"),
        "quality": _user(f"qualite@{DOMAIN}", "Marc Qualité"),
        "tech1": _user(f"tech1@{DOMAIN}", "Julie Technicienne"),
        "tech2": _user(f"tech2@{DOMAIN}", "Paul Technicien"),
        "reader": _user(f"lecteur@{DOMAIN}", "Anne Lecture"),
        "consultant": _user(f"consultant@{DOMAIN}", "Hugo Consultant (2 labos)"),
        "admin_b": _user(f"admin-eaux@{DOMAIN}", "Sophie Admin Eaux"),
    }
    lab_a, _ = auth_service.create_laboratory("Laboratoire Vétérinaire des Alpes", users["admin"].email,
                                              users["admin"].full_name, None)
    lab_b, _ = auth_service.create_laboratory("Laboratoire des Eaux du Rhône", users["admin_b"].email,
                                              users["admin_b"].full_name, None)
    db.session.commit()

    _seed_lab_a(lab_a, users)
    _seed_lab_b(lab_b, users)
    return {
        "password": PASSWORD,
        "users": [
            (users["admin"].email, "Administrateur — Labo vétérinaire (complet)"),
            (users["quality"].email, "Responsable qualité — Labo vétérinaire"),
            (users["tech1"].email, "Technicienne — Labo vétérinaire"),
            (users["tech2"].email, "Technicien — Labo vétérinaire"),
            (users["reader"].email, "Lecture seule — Labo vétérinaire"),
            (users["consultant"].email, "Consultant qualité — membre des DEUX labos"),
            (users["admin_b"].email, "Administratrice — Labo des eaux"),
        ],
    }


def _seed_lab_a(lab: Laboratory, users: dict) -> None:
    from app.models.tenant import Team
    from app.services import ciq_service, lab_service, metrology_service, transmission_service
    from app.services.ciq_service import EntryInput

    with tenant_context(lab.id):
        auto, manual = Team(name="Poste automates", description="Plateau automatisé"), Team(name="Poste manuel")
        db.session.add_all([auto, manual])
        db.session.flush()
        _member(users["quality"], "quality")
        _member(users["tech1"], "technician", auto.id)
        _member(users["tech2"], "technician", manual.id)
        _member(users["reader"], "reader")
        _member(users["consultant"], "quality")
        lab.legal_info = {"siret": "123 456 789 00012", "address": "12 route du Col, 38000 Grenoble"}
        db.session.commit()

    with acting_as(users["admin"], lab.id):
        cats = {name: lab_service.add_category(name) for name in
                ("Analyseur", "Spectrophotomètre", "Balance", "Enceinte thermostatée", "Pipette")}
        eq = {}
        specs = [
            ("analyzer", "Analyseur de biochimie", "BIO-01", "Analyseur", "Roche", "cobas c 311", "high"),
            ("spectro", "Spectrophotomètre UV-Visible", "SPE-01", "Spectrophotomètre", "Hach", "DR6000", "high"),
            ("balance", "Balance de précision", "BAL-01", "Balance", "Mettler Toledo", "XS205", "medium"),
            ("oven", "Étuve 37 °C", "ETU-01", "Enceinte thermostatée", "Memmert", "IN55", "medium"),
            ("pipette", "Pipette 100-1000 µL", "PIP-07", "Pipette", "Eppendorf", "Research plus", "low"),
        ]
        for key, name, iid, cat, manu, model, crit in specs:
            eq[key] = metrology_service.save_equipment(None, {
                "name": name, "internal_id": iid, "category_id": cats[cat].id, "manufacturer": manu,
                "model": model, "serial_number": f"SN-{iid}-2024", "location": "Salle technique",
                "commissioned_on": date(2024, 3, 1), "status": "in_service", "criticality": crit,
                "responsible_id": users["quality"].id, "notes": None,
            })
        today = date.today()
        plans = [
            ("analyzer", "preventive", 6, "month", 20, "Roche Diagnostics"),       # J-30
            ("spectro", "calibration", 1, "year", 5, "Métrologie Rhône-Alpes"),    # J-7
            ("balance", "verification", 3, "month", 0, None),                      # J0
            ("oven", "verification", 1, "month", -10, None),                       # en retard
            ("pipette", "calibration", 1, "year", 200, "Eppendorf Service"),       # à jour
        ]
        for key, etype, value, unit, due_in, provider in plans:
            due = today + timedelta(days=due_in)
            metrology_service.save_plan(eq[key], None, {
                "event_type": etype, "period_value": value, "period_unit": unit, "provider": provider,
                "responsible_id": users["tech1"].id, "last_done_on": None, "next_due_on": due,
                "comment": None, "is_active": True,
            })
        from app.models.equipment import MaintenancePlan
        from app.repositories.base import repo

        cal = repo(MaintenancePlan).first(MaintenancePlan.equipment_id == eq["pipette"].id)
        metrology_service.record_event(eq["pipette"], cal, event_type="calibration",
                                       performed_on=today - timedelta(days=165), outcome="conform",
                                       provider="Eppendorf Service", comment="Étalonnage annuel conforme.",
                                       attachment=None)
        metrology_service.record_event(eq["balance"], None, event_type="corrective",
                                       performed_on=today - timedelta(days=40), outcome="conform", provider=None,
                                       comment="Remplacement du pare-brise, vérification OK.", attachment=None)

    # ---- CIQ Westgard : glucose, 2 niveaux -------------------------------------------------
    with acting_as(users["quality"], lab.id):
        glucose = ciq_service.save_parameter(None, equipment_id=eq["analyzer"].id, name="Glucose", unit="mmol/L",
                                             decimals=2, is_active=True, mode="westgard")
        n1 = ciq_service.save_level(glucose, None, label="N1", sort_order=1, mode=None)
        n2 = ciq_service.save_level(glucose, None, label="N2", sort_order=2, mode=None)
        start = today - timedelta(days=80)
        lot1 = ciq_service.save_lot(n1, None, lot_number="PCCC1-2025-11", manufacturer="Roche",
                                    expires_on=today + timedelta(days=200), in_use_from=start, in_use_to=None)
        lot2 = ciq_service.save_lot(n2, None, lot_number="PCCC2-2025-11", manufacturer="Roche",
                                    expires_on=today + timedelta(days=200), in_use_from=start, in_use_to=None)
        lim1 = ciq_service.set_limits(lot1, mode="westgard", mean=Decimal("5.50"), sd=Decimal("0.15"),
                                      source="supplier", reason=None)
        lim2 = ciq_service.set_limits(lot2, mode="westgard", mean=Decimal("16.00"), sd=Decimal("0.40"),
                                      source="supplier", reason=None)

    n1_pattern = [0.6, -0.5, 0.3, -0.8, 1.1, -0.2, 0.4, -1.0]
    n2_pattern = [-0.4, 0.5, -0.9, 0.2, -0.3, 0.8, -0.6, 0.1]
    scenarios: dict[int, tuple[float | None, float | None]] = {
        22: (2.3, 0.1),      # 1-2s (alerte)
        26: (3.4, -0.2),     # 1-3s (rejet)
        30: (2.4, 2.2),      # 2-2s intra-série (rejet)
        34: (2.5, -2.3),     # R-4s (rejet)
        38: (1.3, None), 39: (1.5, None), 40: (1.2, None), 41: (1.4, None),  # 4-1s
        44: (2.2, None), 45: (2.3, None),  # 2-2s inter-séries
        68: (None, 3.3),     # 1-3s laissé non traité
    }
    for i, z in zip(range(48, 58), [0.3, 0.5, 0.2, 0.7, 0.4, 0.6, 0.3, 0.8, 0.5, 0.4]):
        scenarios[i] = (None, z)  # 10x sur N2
    runs: dict[int, CIQRun] = {}
    lot1_current, lim1_current = lot1, lim1
    technicians = [users["tech1"], users["tech2"]]
    for i in range(70):
        days_ago = 70 - i
        if i == 60:
            # Changement de lot N1 : nouveau lot, nouvelles limites ; les séquences repartent à zéro.
            with acting_as(users["quality"], lab.id):
                lot1.in_use_to = today - timedelta(days=days_ago + 1)
                db.session.commit()
                lot1_current = ciq_service.save_lot(n1, None, lot_number="PCCC1-2026-03", manufacturer="Roche",
                                                    expires_on=today + timedelta(days=300),
                                                    in_use_from=today - timedelta(days=days_ago), in_use_to=None)
                lim1_current = ciq_service.set_limits(lot1_current, mode="westgard", mean=Decimal("5.62"),
                                                      sd=Decimal("0.14"), source="supplier", reason=None)
        z1 = n1_pattern[i % 8]
        z2 = n2_pattern[i % 8]
        s1, s2 = scenarios.get(i, (None, None))
        z1 = s1 if s1 is not None else z1
        z2 = s2 if s2 is not None else z2
        operator = technicians[i % 2]
        with acting_as(operator, lab.id):
            level1 = db.session.scalar(sa.select(ControlLevel).where(ControlLevel.id == n1.id))
            level2 = db.session.scalar(sa.select(ControlLevel).where(ControlLevel.id == n2.id))
            entries = [
                EntryInput(level1, lot1_current, _value(lim1_current.mean, lim1_current.sd, z1)),
                EntryInput(level2, lot2, _value(lim2.mean, lim2.sd, z2)),
            ]
            runs[i] = _run(glucose, _day(days_ago), entries, operator)

    # Annulation d'une saisie erronée puis ressaisie
    with acting_as(users["quality"], lab.id):
        from app.models.ciq import CIQResult

        wrong = db.session.scalar(sa.select(CIQResult).where(CIQResult.run_id == runs[50].id,
                                                             CIQResult.level_id == n1.id))
        ciq_service.void_result(wrong, "Erreur de saisie (inversion de chiffres).")
        run50 = db.session.scalar(sa.select(CIQRun).where(CIQRun.id == runs[50].id))
        level1 = db.session.scalar(sa.select(ControlLevel).where(ControlLevel.id == n1.id))
        ciq_service.add_result(run50, EntryInput(level1, lot1, _value(lim1.mean, lim1.sd, -0.6),
                                                 "Ressaisie après correction."))

    # Traitement des rejets : justification + actions correctives
    from app.services import corrective_action_service as cas

    handled = [(26, "Calibration refaite, contrôle repassé conforme.", 20, "validated"),
               (30, "Réactif en fin de flacon remplacé.", 10, "done"),
               (34, "Maintenance de l'aiguille de prélèvement programmée.", -5, "open"),
               (45, "Dérive constatée : recalibration et suivi renforcé.", 15, "open")]
    for idx, description, due_offset, final in handled:
        with acting_as(users["quality"], lab.id):
            run = db.session.scalar(sa.select(CIQRun).where(CIQRun.id == runs[idx].id))
            if run.status != "rejected":
                continue
            action = ciq_service.justify_run(run, justification="Analyse du rejet : " + description,
                                             action_description=description, responsible_id=users["tech1"].id,
                                             due_on=today + timedelta(days=due_offset))
        if final in ("done", "validated"):
            with acting_as(users["tech1"], lab.id):
                from app.models.actions import CorrectiveAction

                a = db.session.scalar(sa.select(CorrectiveAction).where(CorrectiveAction.id == action.id))
                cas.complete(a, today - timedelta(days=2), "Action réalisée et vérifiée.")
        if final == "validated":
            with acting_as(users["quality"], lab.id):
                a = db.session.scalar(sa.select(CorrectiveAction).where(CorrectiveAction.id == action.id))
                cas.validate(a, "Efficacité vérifiée sur les séries suivantes.")

    # ---- CIQ Shewhart : nitrates, carte de contrôle ----------------------------------------
    with acting_as(users["quality"], lab.id):
        nitrates = ciq_service.save_parameter(None, equipment_id=eq["spectro"].id, name="Nitrates", unit="mg/L",
                                              decimals=2, is_active=True, mode="shewhart")
        level = ciq_service.save_level(nitrates, None, label="Contrôle 10 mg/L", sort_order=1, mode=None)
        lot = ciq_service.save_lot(level, None, lot_number="NIT-2026-01", manufacturer="Préparation interne",
                                   expires_on=today + timedelta(days=120), in_use_from=today - timedelta(days=90),
                                   in_use_to=None)
        provisional = ciq_service.set_limits(lot, mode="shewhart", mean=Decimal("10.00"), sd=Decimal("0.25"),
                                             source="lab", reason=None)
    reference_z = [0.2, -0.5, 1.1, -0.8, 0.3, -1.2, 0.7, -0.1, 0.9, -0.6, 0.4, -0.3, 1.4, -0.9, 0.1, -0.4, 0.6,
                   -1.0, 0.5, -0.2, 0.8, -0.7, 0.3, -1.3, 1.0]
    day = 88
    for z in reference_z:
        with acting_as(users["tech2"], lab.id):
            lvl = db.session.scalar(sa.select(ControlLevel).where(ControlLevel.id == level.id))
            _run(nitrates, _day(day, 10), [EntryInput(lvl, lot, _value(provisional.mean, provisional.sd, z))],
                 users["tech2"])
        day -= 1
    with acting_as(users["quality"], lab.id):
        computed = ciq_service.compute_limits_from_reference(
            lot, mode="shewhart", ref_from=_day(89, 0), ref_to=_day(day + 1, 23),
            reason="Période de référence initiale (25 valeurs).", min_points=20)
    shewhart_z = ([0.4, -0.5, 0.6, -0.3, 0.2, -0.6, 0.5, -0.4]
                  + [3.4]                                   # A : hors limites d'action
                  + [-0.5, 0.4]
                  + [2.5, -0.3, 2.4]                        # B : 2 sur 3 hors ±2s
                  + [-0.6, 0.5, -0.4]
                  + [0.3, 0.5, 0.4, 0.2, 0.6, 0.3, 0.5]     # C : 7 du même côté
                  + [-0.5, 0.4]
                  + [-0.4, -0.3, -0.5, -0.2, -0.6, 0.2, -0.3, -0.4, -0.5, -0.2, -0.3]  # D : 10 sur 11
                  + [0.5, -0.2])
    for z in shewhart_z:
        with acting_as(users["tech2"], lab.id):
            lvl = db.session.scalar(sa.select(ControlLevel).where(ControlLevel.id == level.id))
            _run(nitrates, _day(day, 10), [EntryInput(lvl, lot, _value(computed.mean, computed.sd, z))],
                 users["tech2"])
        day -= 1
        if day < 1:
            day = 1

    # ---- V1.1 : transmissions et non-conformités ------------------------------------------
    with acting_as(users["tech1"], lab.id):
        from app.models.tenant import Team as TeamModel

        team_auto = db.session.scalar(sa.select(TeamModel).where(TeamModel.name == "Poste automates"))
        transmission_service.create(title="Réactif glucose : nouveau flacon entamé", body="Flacon ouvert ce matin, "
                                    "calibration faite, CIQ conformes.", category="reagents", priority="normal",
                                    due_on=None, user_ids=[users["tech2"].id], team_ids=[], attachments=[])
        transmission_service.create(title="Étuve ETU-01 : température instable", body="Relevé à 38,6 °C à 14 h. "
                                    "Merci de surveiller et de prévenir la qualité.", category="equipment",
                                    priority="urgent", due_on=today + timedelta(days=1),
                                    user_ids=[users["quality"].id], team_ids=[team_auto.id], attachments=[])
    with acting_as(users["quality"], lab.id):
        from app.services import nc_service

        run30 = db.session.scalar(sa.select(CIQRun).where(CIQRun.id == runs[30].id))
        nc = nc_service.create_from_run(run30)
        nc_service.change_status(nc, "open", "Ouverture après rejet CIQ.")
        nc_service.create({"title": "Vérification de l'étuve non réalisée à temps",
                           "description": "La vérification mensuelle de l'étuve ETU-01 est en retard.",
                           "origin": "metrology", "severity": "minor", "equipment_id": eq["oven"].id,
                           "detected_on": today, "responsible_id": users["quality"].id})

    # Notifications initiales pour le tableau de bord
    with tenant_context(lab.id):
        from app.services.notification_service import generate_for_current_lab

        generate_for_current_lab(date.today())
        db.session.commit()


def _seed_lab_b(lab: Laboratory, users: dict) -> None:
    from app.services import ciq_service, metrology_service
    from app.services.ciq_service import EntryInput

    with tenant_context(lab.id):
        _member(users["consultant"], "reader")
        sub = db.session.scalar(sa.select(Subscription))
        sub.status, sub.plan = "active", "yearly"
        sub.stripe_customer_id, sub.stripe_subscription_id = "cus_demo_eaux", "sub_demo_eaux"
        sub.current_period_end = utcnow() + timedelta(days=200)
        db.session.commit()
    with acting_as(users["admin_b"], lab.id):
        spectro = metrology_service.save_equipment(None, {
            "name": "Spectrophotomètre eaux", "internal_id": "EAU-SPE-01", "category_id": None,
            "manufacturer": "Hach", "model": "DR3900", "serial_number": None, "location": "Paillasse 2",
            "commissioned_on": None, "status": "in_service", "criticality": "high", "responsible_id": None,
            "notes": None,
        })
        param = ciq_service.save_parameter(None, equipment_id=spectro.id, name="Phosphates", unit="mg/L",
                                           decimals=3, is_active=True, mode="shewhart")
        lvl = ciq_service.save_level(param, None, label="Étalon 1 mg/L", sort_order=1, mode=None)
        lot = ciq_service.save_lot(lvl, None, lot_number="PHO-01", manufacturer=None, expires_on=None,
                                   in_use_from=None, in_use_to=None)
        lim = ciq_service.set_limits(lot, mode="shewhart", mean=Decimal("1.000"), sd=Decimal("0.020"),
                                     source="lab", reason=None)
        for i, z in enumerate([0.3, -0.4, 0.8, -0.2, 0.1]):
            level = db.session.scalar(sa.select(ControlLevel).where(ControlLevel.id == lvl.id))
            _run(param, _day(10 - i), [EntryInput(level, lot, _value(lim.mean, lim.sd, z))], users["admin_b"])
