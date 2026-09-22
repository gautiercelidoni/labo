"""Abonnement : page d'état, Checkout, portail client et réception des webhooks Stripe."""
from __future__ import annotations

from flask import abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_wtf import FlaskForm
from wtforms import HiddenField

from app.blueprints.auth.forms import EmptyForm
from app.blueprints.billing import bp
from app.extensions import csrf, db
from app.models.billing import PLAN_LABELS
from app.security.permissions import P, require
from app.services import membership_service, stripe_service
from app.services.stripe_service import BillingError, InvalidSignature


class CheckoutForm(FlaskForm):
    plan = HiddenField()


@bp.route("/abonnement")
@require(P.BILLING_MANAGE)
def index():
    cfg = current_app.config
    return render_template(
        "billing/index.html",
        subscription=stripe_service.current_subscription(),
        monthly_form=CheckoutForm(plan="monthly"),
        yearly_form=CheckoutForm(plan="yearly"),
        portal_form=EmptyForm(),
        plan_labels=PLAN_LABELS,
        stripe_ready=bool(cfg["STRIPE_SECRET_KEY"] and cfg["STRIPE_PRICE_MONTHLY"]),
        active_members=membership_service.active_member_count(),
        tax_enabled=cfg["STRIPE_TAX_ENABLED"],
        vat_mention=cfg["VAT_EXEMPTION_MENTION"],
        grace_days=cfg["GRACE_DAYS"],
    )


@bp.route("/abonnement/souscrire", methods=["POST"])
@require(P.BILLING_MANAGE)
def checkout():
    form = CheckoutForm()
    if not form.validate_on_submit() or form.plan.data not in PLAN_LABELS:
        abort(400)
    try:
        url = stripe_service.create_checkout_url(form.plan.data)
    except BillingError as e:
        db.session.rollback()
        flash(str(e), "danger")
        return redirect(url_for("billing.index"))
    except Exception:  # erreur réseau ou Stripe : message générique, détail journalisé
        db.session.rollback()
        current_app.logger.exception("Échec de création de la session Checkout")
        flash("Le service de paiement est momentanément indisponible.", "danger")
        return redirect(url_for("billing.index"))
    return redirect(url, code=303)


@bp.route("/abonnement/portail", methods=["POST"])
@require(P.BILLING_MANAGE)
def portal():
    if not EmptyForm().validate_on_submit():
        abort(400)
    try:
        url = stripe_service.create_portal_url()
    except BillingError as e:
        flash(str(e), "danger")
        return redirect(url_for("billing.index"))
    except Exception:
        current_app.logger.exception("Échec d'ouverture du portail Stripe")
        flash("Le service de paiement est momentanément indisponible.", "danger")
        return redirect(url_for("billing.index"))
    return redirect(url, code=303)


@bp.route("/abonnement/retour")
@require(P.BILLING_MANAGE)
def checkout_return():
    # Le statut n'est jamais déduit du retour navigateur : seul le webhook fait foi.
    if request.args.get("statut") == "succes":
        flash("Paiement transmis à Stripe. Le statut de l'abonnement sera mis à jour dès sa confirmation.", "info")
    else:
        flash("Souscription annulée.", "info")
    return redirect(url_for("billing.index"))


@bp.route("/stripe/webhook", methods=["POST"])
@csrf.exempt
def stripe_webhook():
    payload = request.get_data(cache=False)
    try:
        event = stripe_service.verify(payload, request.headers.get("Stripe-Signature"))
    except InvalidSignature:
        current_app.logger.warning("Webhook Stripe rejeté : signature invalide")
        return jsonify({"erreur": "signature invalide"}), 400
    outcome = stripe_service.handle_event(event)
    current_app.logger.info("Webhook Stripe %s (%s) : %s", event.get("id"), event.get("type"), outcome)
    return jsonify({"resultat": outcome}), 200
