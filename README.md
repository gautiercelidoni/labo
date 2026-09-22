# LabQualité

SaaS de gestion de la qualité pour petits laboratoires accrédités ou engagés dans une démarche
ISO/IEC 17025 (vétérinaires, environnement, agroalimentaire, analyse des eaux), de 2 à 15 utilisateurs
par laboratoire.

> LabQualité aide à structurer le système qualité ; il ne garantit pas à lui seul la conformité
> ISO/IEC 17025 ni l'accréditation COFRAC.

## Fonctionnalités

**V1**

- Comptes, invitations, **un utilisateur peut appartenir à plusieurs laboratoires** (rôle distinct par
  laboratoire, sélecteur de laboratoire actif) ; rôles Administrateur, Responsable qualité,
  Technicien, Lecture seule.
- **Multi-tenant strict** : filtre ORM automatique, contrôle à l'écriture, clés étrangères composites
  en base, 404 sur toute ressource d'un autre laboratoire.
- **CIQ** : paramètres, niveaux, lots, jeux de limites figés ; mode **Westgard** (1-2s, 1-3s, 2-2s,
  R-4s, 4-1s, 10x) ou **carte de contrôle Shewhart** (règles A à D, limites calculées sur période de
  référence) ; saisie par série, commentaire obligatoire configurable, annulation/ressaisie, rejet avec
  justification et action corrective, graphiques Levey-Jennings / cartes de contrôle (Chart.js local),
  historique filtrable, exports CSV et rapport PDF mensuel. Spécification : [docs/regles-ciq.md](docs/regles-ciq.md).
- **Métrologie** : parc d'équipements, plans (étalonnage, vérification, maintenance…), réalisations
  avec certificats, échéances recalculées depuis la date effective, alertes J-30/J-7/J0/retard,
  calendrier, suspension proposée après un étalonnage non conforme, planning et historique PDF.
- **Tableau de bord** par rôle, **notifications** (application + email) générées par une commande cron.
- **Abonnement Stripe** : forfait par laboratoire mensuel ou annuel, essai 30 jours, portail client,
  webhooks signés et idempotents, lecture seule après expiration (aucune donnée supprimée).
- **Journal d'audit** en ajout seul, protégé au niveau PostgreSQL.
- **Exports** CSV compatibles Excel français (anti-injection de formules) et PDF A4.

**V1.1** (développée)

- **Cahier de transmission** : destinataires personnes ou équipes/postes, priorités, accusés de lecture
  horodatés, suivi, archivage (jamais de suppression).
- **Non-conformités** : numérotation par laboratoire et par année, cycle de vie complet, clôture
  contrôlée (analyse de cause, action corrective ou justification, vérification d'efficacité, responsable
  qualité), conversion d'un rejet CIQ en NC, fiche et liste PDF.

## Prérequis

- Python 3.12, PostgreSQL 16 (extension `citext`, fournie en standard)
- Bibliothèques système : Pango (WeasyPrint) et libmagic
  (Debian/Ubuntu : `apt install libpango-1.0-0 libpangoft2-1.0-0 libmagic1 fonts-dejavu-core`)
- Ou simplement Docker et Docker Compose

## Démarrage rapide avec Docker Compose

```bash
cp .env.example .env
docker compose up --build            # base, application (port 8000) et planificateur
docker compose exec app flask seed-demo
```

Ouvrir http://localhost:8000 et se connecter avec `admin@demo.labqualite.fr` / `Demo-Labqualite-2026`
(autres comptes : `qualite@`, `tech1@`, `tech2@`, `lecteur@`, `consultant@` — ce dernier est membre des
deux laboratoires de démonstration). Les identifiants de démonstration ne sont affichés qu'en
développement.

## Installation locale sans Docker

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[test]"
cp .env.example .env                 # adapter DATABASE_URL, DATABASE_OWNER_URL, SECRET_KEY…
```

### Base de données

Deux rôles distincts (le rôle applicatif ne peut ni modifier le schéma, ni modifier l'audit) :

```sql
CREATE ROLE labq_owner LOGIN PASSWORD '…';
CREATE ROLE labq_app LOGIN PASSWORD '…';
CREATE DATABASE labqualite OWNER labq_owner;
CREATE DATABASE labqualite_test OWNER labq_owner;   -- pour les tests
```

### Migrations

```bash
export FLASK_APP=app:create_app
.venv/bin/flask db upgrade           # utilise DATABASE_OWNER_URL et applique les droits de DB_APP_ROLE
```

Après une modification des modèles : `flask db migrate -m "description"` puis relire le fichier généré.

### Lancement

```bash
.venv/bin/flask seed-demo            # données de démonstration (2 laboratoires)
.venv/bin/flask run --debug          # http://localhost:5000
# ou en conditions proches de la production :
.venv/bin/gunicorn -c gunicorn.conf.py "app:create_app()"
```

## Tests

```bash
.venv/bin/pytest                     # ~290 tests, base PostgreSQL de test recréée et migrée automatiquement
.venv/bin/pytest --cov=app           # couverture
.venv/bin/pytest -m "not slow"       # sans le test complet des données de démonstration
```

Les tests utilisent `TEST_DATABASE_URL` / `TEST_DATABASE_OWNER_URL` (valeurs par défaut dans
`app/config.py`). Chaque test s'exécute dans une transaction annulée. Couverture : moteur de règles
(chaque règle, limites exactes, lots, limites, annulations, séquences insuffisantes, multi-niveaux),
isolation multi-tenant (ORM, contraintes, IDOR HTTP, exports, tableau de bord, multi-labos, retrait
d'accès), autorisations par rôle, authentification (blocage, jetons, sessions, CSRF), fichiers,
injection CSV, webhooks Stripe, immutabilité de l'audit, services métier, exports PDF.

## Variables d'environnement

Toutes documentées dans [.env.example](.env.example). Principales :

| Variable | Rôle |
|---|---|
| `APP_ENV` | `development`, `test` ou `production` |
| `SECRET_KEY` | clé de signature des sessions (obligatoire hors développement) |
| `BASE_URL` | URL publique (liens des emails, retours Stripe) |
| `DATABASE_URL` | connexion du rôle **applicatif** |
| `DATABASE_OWNER_URL`, `DB_APP_ROLE` | rôle propriétaire (migrations) et rôle à qui accorder les droits |
| `MAIL_BACKEND`, `SMTP_*`, `MAIL_FROM` | emails (`console` en développement) |
| `STORAGE_BACKEND`, `UPLOAD_DIR`, `S3_*`, `MAX_UPLOAD_MB` | pièces jointes |
| `STRIPE_*`, `TRIAL_DAYS`, `GRACE_DAYS`, `DEFAULT_MAX_ACTIVE_USERS` | abonnement |
| `VAT_EXEMPTION_MENTION`, `INVOICE_ISSUER_*` | mentions légales des factures |
| `SELF_SIGNUP_ENABLED` | création de laboratoire en libre-service (désactivée par défaut) |
| `LOGIN_MAX_FAILURES`, `LOGIN_WINDOW_MINUTES` | limitation des tentatives de connexion |

## Emails

`MAIL_BACKEND=smtp` avec `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_USE_TLS`
(STARTTLS) ou `SMTP_USE_SSL`. Emails envoyés : réinitialisation de mot de passe (lien valable 60 min,
usage unique), invitations (7 jours), récapitulatifs d'alertes. Un échec d'envoi est journalisé sans
bloquer l'action métier.

## Stripe

1. Créer un produit « Forfait laboratoire » avec deux prix récurrents (mensuel, annuel) ; renseigner
   `STRIPE_PRICE_MONTHLY` et `STRIPE_PRICE_YEARLY` (aucun montant n'est codé en dur).
2. `STRIPE_SECRET_KEY` (clé `sk_test_…` en mode test).
3. Webhook vers `https://votre-domaine/stripe/webhook` avec les événements
   `checkout.session.completed`, `customer.subscription.created|updated|deleted`, `invoice.paid`,
   `invoice.payment_failed` ; renseigner `STRIPE_WEBHOOK_SECRET`.
   En local : `stripe listen --forward-to localhost:8000/stripe/webhook`.
4. Activer le portail client dans le tableau de bord Stripe.

Fonctionnement : chaque laboratoire démarre en essai de `TRIAL_DAYS` jours. L'état de l'abonnement
provient **uniquement des webhooks** (le retour navigateur n'est jamais pris en compte). Paiement en
retard : période de grâce de `GRACE_DAYS` jours, puis lecture seule. Résiliation : accès en écriture
jusqu'à la fin de la période payée, puis lecture seule. Les données ne sont jamais supprimées
automatiquement. Le plafond d'utilisateurs actifs (`max_active_users`, 15 par défaut) est contrôlé aux
invitations et réactivations.

**TVA et mentions légales (France)** : avec `STRIPE_TAX_ENABLED=true`, Stripe Tax calcule la TVA
(collecte de l'adresse et du numéro de TVA intracommunautaire au Checkout). Sinon, la mention
`VAT_EXEMPTION_MENTION` (« TVA non applicable, art. 293 B du CGI » par défaut) et les informations de
l'émetteur (`INVOICE_ISSUER_NAME`, `INVOICE_ISSUER_SIRET`, `INVOICE_ISSUER_ADDRESS`) sont inscrites dans
le pied de page des factures Stripe du client.

## Stockage des fichiers

- Développement : `STORAGE_BACKEND=local`, fichiers sous `UPLOAD_DIR/{laboratoire}/{uuid}` (hors du
  dossier public, nom d'origine jamais utilisé comme chemin).
- Production : `STORAGE_BACKEND=s3` (Scaleway Object Storage, OVHcloud…), téléchargement par URL
  présignée de 60 s générée **après** contrôle d'accès.
- Liste blanche : PDF, PNG, JPEG, DOCX, XLSX, CSV ; type détecté sur le contenu (octets magiques) ;
  taille maximale `MAX_UPLOAD_MB` (15 Mo) ; téléchargement forcé (`Content-Disposition: attachment`,
  `nosniff`) et audité.

## Création du premier laboratoire

La création en libre-service est désactivée par défaut. En tant qu'opérateur de la plateforme :

```bash
flask create-lab --name "Laboratoire des Eaux" --admin-email responsable@labo.fr --admin-name "Nom Prénom"
```

L'administrateur invite ensuite ses collègues depuis *Administration > Membres*. Pour ouvrir
l'inscription publique avec essai gratuit : `SELF_SIGNUP_ENABLED=true`.

## Sauvegarde, restauration et production

Voir [docs/exploitation.md](docs/exploitation.md) : Docker Compose de production (nginx TLS, Gunicorn,
planificateur, sauvegardes quotidiennes avec rétention), rôles PostgreSQL, cron, restauration,
mises à jour, supervision (`/sante`, logs JSON), rotation des secrets.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Hébergement recommandé en France/UE (Scaleway, OVHcloud, Clever Cloud). Aucune donnée de santé
humaine n'est traitée : pas d'exigence HDS.

## Architecture

Monolithe Flask modulaire (application factory, blueprints), services métier, dépôt tenant-scoped,
domaine pur pour les règles CIQ et les échéances. Détails et décisions : [docs/ETAPE-1-architecture.md](docs/ETAPE-1-architecture.md).

```
app/
  security/     tenancy (isolation), permissions, mots de passe, jetons, limitation, en-têtes
  models/       SQLAlchemy (UUID, contraintes composites tenant)
  domain/       règles Westgard/Shewhart, échéances (sans Flask ni base)
  services/     logique métier + audit dans la même transaction
  blueprints/   routes (aucune requête SQL directe)
  templates/    Jinja2 (dont gabarits PDF), static/ (CSS, JS, htmx et Chart.js locaux)
migrations/     Alembic
tests/          pytest
deploy/         entrypoint, init PostgreSQL, planificateur, sauvegardes, nginx
```

## Limites connues

- Pas de Row Level Security PostgreSQL en V1 (décision validée) : l'isolation repose sur le filtre ORM,
  le contrôle au flush et les clés étrangères composites ; le schéma reste compatible RLS.
- Les requêtes SQL brutes (`text()`) ne sont pas filtrées automatiquement : elles sont réservées aux
  verrous techniques et aux migrations.
- Interface d'administration de la plateforme (liste de tous les laboratoires, suspension) en ligne
  de commande uniquement.
- Anonymisation d'un utilisateur : commande `flask rgpd anonymiser-utilisateur`, pas d'écran dédié.
- Notifications par email groupées par exécution du planificateur (toutes les heures), pas en temps réel.
- Les rapports PDF tracent les graphiques en z-score (lisibles quels que soient les jeux de limites).
- Pas d'application mobile, pas de connexion aux automates (hors périmètre).
