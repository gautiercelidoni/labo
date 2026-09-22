# Étape 1 — Architecture et modèle de données

Statut : **proposition à valider** avant l'étape 2.

## 1. Architecture générale

Monolithe Flask modulaire, un seul processus applicatif (Gunicorn, 2 à 4 workers), une base PostgreSQL, un stockage de fichiers (disque local en dev, S3 compatible en prod), un cron externe pour les notifications. Pas de file de messages, pas de cache distribué.

```
Navigateur ──HTTPS──> Nginx/Traefik ──> Gunicorn (Flask)
                                          ├── PostgreSQL 16
                                          ├── Stockage fichiers (local / S3)
                                          ├── SMTP (emails)
                                          └── Stripe API  <── webhooks Stripe
Cron (hôte ou conteneur dédié) ──> flask notifications run
```

Couches, de l'extérieur vers l'intérieur :

1. **Routes (blueprints)** : parsing de la requête, formulaire, appel d'un service, rendu. Aucune requête SQL directe.
2. **Services** : logique métier, transactions, appels d'audit.
3. **Repositories tenant-scoped** : accès aux données, filtrage tenant garanti.
4. **Modèles SQLAlchemy** : schéma, contraintes.
5. **Domaine pur** (`app/domain/`) : moteur de règles CIQ, calcul d'échéances. Aucune dépendance à Flask ni à la base : testable en isolation.

## 2. Arborescence cible

```
labqualite/
├── CLAUDE.md
├── SPEC.md
├── README.md
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── docker-compose.prod.yml
├── pyproject.toml
├── gunicorn.conf.py
├── migrations/                      # Alembic
├── docs/
│   ├── ETAPE-1-architecture.md
│   ├── regles-ciq.md                # spécification exacte des règles
│   └── exploitation.md              # sauvegardes, restauration, cron
├── app/
│   ├── __init__.py                  # create_app()
│   ├── config.py                    # Dev / Test / Prod
│   ├── extensions.py                # db, migrate, login_manager, csrf
│   ├── cli.py                       # seed-demo, notifications, recalcul échéances
│   ├── security/
│   │   ├── tenancy.py               # contexte tenant + filtre ORM automatique
│   │   ├── permissions.py           # Permission, ROLE_PERMISSIONS, @require()
│   │   ├── passwords.py             # Argon2id
│   │   ├── tokens.py                # jetons signés usage unique
│   │   ├── rate_limit.py            # compteur d'échecs en base
│   │   └── headers.py               # CSP, HSTS, etc.
│   ├── models/
│   │   ├── base.py                  # Base, UUIDPk, Timestamps, TenantScoped
│   │   ├── tenant.py                # Laboratory, Membership, Team, Invitation
│   │   ├── user.py                  # User, LoginAttempt, AuthToken
│   │   ├── audit.py                 # AuditEvent
│   │   ├── files.py                 # Attachment
│   │   ├── equipment.py             # Equipment, MaintenancePlan, MaintenanceEvent
│   │   ├── ciq.py                   # paramètres, niveaux, lots, limites, runs, résultats
│   │   ├── actions.py               # CorrectiveAction
│   │   ├── billing.py               # Subscription, StripeEvent
│   │   ├── notifications.py
│   │   ├── transmissions.py         # V1.1
│   │   └── non_conformities.py      # V1.1
│   ├── repositories/
│   │   └── base.py                  # TenantRepository[T]
│   ├── domain/
│   │   ├── ciq_rules/
│   │   │   ├── types.py             # Point, RuleHit, Evaluation
│   │   │   ├── westgard.py
│   │   │   ├── shewhart.py
│   │   │   └── engine.py
│   │   └── scheduling.py            # calcul des échéances
│   ├── services/
│   │   ├── audit_service.py
│   │   ├── auth_service.py
│   │   ├── membership_service.py
│   │   ├── ciq_service.py
│   │   ├── metrology_service.py
│   │   ├── corrective_action_service.py
│   │   ├── dashboard_service.py
│   │   ├── export_pdf.py
│   │   ├── export_csv.py
│   │   ├── file_storage.py          # LocalStorage / S3Storage
│   │   ├── email_service.py
│   │   ├── notification_service.py
│   │   └── stripe_service.py
│   ├── blueprints/
│   │   ├── auth/  dashboard/  admin/  ciq/  metrologie/
│   │   ├── billing/  audit/
│   │   └── transmissions/  non_conformites/        # V1.1
│   │       (chacun : __init__.py, routes.py, forms.py)
│   ├── templates/
│   │   ├── base.html, _macros.html, _pagination.html
│   │   ├── <un dossier par blueprint>/
│   │   └── pdf/                     # gabarits d'impression A4
│   └── static/
│       ├── css/app.css
│       ├── js/app.js, charts.js
│       └── vendor/htmx.min.js, chart.umd.min.js   # servis en local
└── tests/
    ├── conftest.py                  # base de test, 2 labos, fabriques
    ├── factories.py
    ├── domain/test_westgard.py, test_shewhart.py, test_scheduling.py
    ├── security/test_tenancy.py, test_permissions.py, test_auth.py,
    │            test_files.py, test_csv_injection.py
    ├── billing/test_webhooks.py
    └── test_audit_immutability.py
```

## 3. Isolation multi-tenant

Trois lignes de défense cumulées.

### 3.1 Contexte de tenant
- Après connexion, `session["active_lab_id"]` est défini (choix explicite si plusieurs appartenances).
- Un `before_request` charge la `Membership` active (`user_id` + `laboratory_id`, `is_active = true`) et la place dans `g.membership`. Si l'appartenance n'existe plus ou a été désactivée : déconnexion du labo, retour au sélecteur. **Le rôle est donc relu à chaque requête** : un retrait de droits est effectif immédiatement.
- `current_tenant_id()` lève une exception si aucun contexte n'est défini. Aucune valeur par défaut.

### 3.2 Filtre ORM automatique
Tous les modèles de labo héritent de `TenantScoped` (colonne `tenant_id` non nulle, indexée). Un événement SQLAlchemy `do_orm_execute` ajoute à **chaque** SELECT ORM :

```python
with_loader_criteria(TenantScoped,
                     lambda cls: cls.tenant_id == current_tenant_id(),
                     include_aliases=True)
```

Un événement `before_flush` vérifie que tout objet `TenantScoped` créé ou modifié porte le `tenant_id` courant, et le remplit à la création. Toute incohérence lève une exception et annule la transaction.

Les commandes CLI (seed, cron) ouvrent explicitement un contexte par labo (`with tenant_context(lab_id):`). Un contexte « système » sans filtre existe uniquement pour les tâches globales (webhooks Stripe, cron multi-labos) et il est interdit dans les routes (vérifié par test).

### 3.3 Contraintes en base
Chaque table tenant-scoped a une contrainte unique `(tenant_id, id)`. Les clés étrangères entre tables d'un même labo sont **composites** :

```sql
FOREIGN KEY (tenant_id, lot_id) REFERENCES control_lot (tenant_id, id)
```

Même avec un bug applicatif, un résultat CIQ du labo A ne peut pas référencer un lot du labo B : PostgreSQL le refuse.

### 3.4 Accès aux ressources par identifiant
`TenantRepository.get_or_404(id)` passe par le filtre : une ressource d'un autre labo renvoie **404** (et non 403, pour ne pas révéler son existence). Utiliser `db.session.get()` sur un modèle tenant-scoped est interdit ; un test statique (grep dans la CI) le vérifie.

### 3.5 Option à valider : Row Level Security PostgreSQL
Une quatrième ligne, avec des politiques RLS fondées sur `SET LOCAL app.tenant_id`, est possible. **Proposition : ne pas l'activer en V1** (complexité des migrations et des tests), mais garder le schéma compatible.

## 4. Modèle de données

Conventions communes :
- `id` UUID (généré côté Python, `uuid4`) ;
- `created_at` et `updated_at` en `timestamptz` UTC ;
- `created_by_id` sur les données qualité ;
- archivage par `archived_at` plutôt que suppression pour toute donnée qualité ;
- énumérations stockées en `varchar` + `CHECK` (plus simple à migrer que les ENUM PostgreSQL) ;
- valeurs numériques CIQ en `numeric(18,6)`, jamais en `float`.

Légende : **G** = globale, **T** = tenant-scoped.

### 4.1 Comptes et laboratoires

| Table | Portée | Champs principaux | Contraintes / index |
|---|---|---|---|
| `laboratory` | G | name, slug, timezone (défaut Europe/Paris), logo_attachment_id, legal_info (jsonb : SIRET, adresse), retention_days, max_active_users, status (active, read_only, suspended) | slug unique |
| `user` | G | email (citext), password_hash, full_name, is_platform_admin, password_changed_at, session_version (int), last_login_at, anonymized_at | email unique |
| `membership` | T | user_id, role (admin, quality, technician, reader), team_id, is_active, joined_at | unique (tenant_id, user_id) ; index (user_id) |
| `team` | T | name, description | unique (tenant_id, name) |
| `invitation` | T | email, role, token_hash, expires_at, accepted_at, invited_by_id | index (tenant_id, email) ; token_hash unique |
| `auth_token` | G | user_id, purpose (reset_password, verify_email), token_hash, expires_at, used_at | token_hash unique |
| `login_attempt` | G | email, ip, success, created_at | index (email, created_at) ; purge > 30 j |

`session_version` : incrémenté au changement de mot de passe ; stocké en session et comparé à chaque requête. Toutes les autres sessions sont ainsi invalidées sans stockage serveur des sessions.

### 4.2 Audit

| Table | Portée | Champs |
|---|---|---|
| `audit_event` | T (tenant_id nullable pour les événements globaux, ex. échec de connexion sur email inconnu) | id bigserial, occurred_at, tenant_id, user_id, action, object_type, object_id, ip, before (jsonb), after (jsonb), reason, request_id |

Index : (tenant_id, occurred_at desc), (tenant_id, object_type, object_id).

### 4.3 Fichiers

| Table | Portée | Champs |
|---|---|---|
| `attachment` | T | storage_key (UUID aléatoire), original_name (affichage uniquement), content_type détecté, size_bytes, sha256, owner_type, owner_id, uploaded_by_id |

Lien polymorphe (`owner_type` + `owner_id`) : un seul point de contrôle d'accès, qui vérifie aussi que l'utilisateur a le droit de lire l'objet propriétaire.

### 4.4 Équipements et métrologie

| Table | Portée | Champs principaux | Contraintes |
|---|---|---|---|
| `equipment` | T | name, category, manufacturer, model, serial_number, internal_id, location, commissioned_on, status (in_service, out_of_service, maintenance, suspended, retired), criticality (low, medium, high), responsible_id, notes, archived_at | unique (tenant_id, internal_id) |
| `maintenance_plan` | T | equipment_id, event_type (calibration, verification, preventive, corrective, intermediate_check, qualification), period_value + period_unit (day, week, month, year), provider, responsible_id, last_done_on, next_due_on, is_active | index (tenant_id, next_due_on) where is_active |
| `maintenance_event` | T | plan_id (nullable pour le correctif non planifié), equipment_id, event_type, performed_on, outcome (conform, non_conform), comment, performed_by_id | index (tenant_id, equipment_id, performed_on) |

La prochaine échéance est recalculée par `domain/scheduling.py` à partir de la **date effective** de réalisation (hypothèse à valider : certains labos calent sur la date prévue).

### 4.5 CIQ

| Table | Portée | Champs principaux | Contraintes |
|---|---|---|---|
| `ciq_parameter` | T | equipment_id, name, unit, decimals, is_active | unique (tenant_id, equipment_id, name) |
| `control_level` | T | parameter_id, label (N1, N2, bas, haut…), order | unique (tenant_id, parameter_id, label) |
| `control_lot` | T | level_id, manufacturer, lot_number, expires_on, in_use_from, in_use_to | unique (tenant_id, level_id, lot_number) |
| `control_limit_set` | T | lot_id, mode (westgard, shewhart), mean, sd, source (supplier, lab, computed), reference_from, reference_to, n_reference, valid_from, valid_to, reason, created_by_id | un seul jeu actif par lot (index unique partiel where valid_to is null) |
| `ciq_rule_config` | T | parameter_id, mode, rules (jsonb : règle → désactivée / avertissement / rejet), comment_required_on (warning, reject), min_reference_points (défaut 20) | unique (tenant_id, parameter_id) |
| `ciq_run` | T | equipment_id, parameter_id, run_at, operator_id, status (open, accepted, rejected, justified) | index (tenant_id, parameter_id, run_at) |
| `ciq_result` | T | run_id, level_id, lot_id, limit_set_id, value, z_score, deviation, status (accepted, warning, rejected), rules_triggered (jsonb), comment, entered_by_id, voided_at, void_reason | unique (tenant_id, run_id, level_id) ; index (tenant_id, lot_id, run_at) |
| `corrective_action` | T | source_type (ciq_run ; V1.1 : non_conformity), source_id, description, responsible_id, due_on, done_on, validated_by_id, validated_at, status (open, done, validated) | index (tenant_id, status, due_on) |

Choix clés :
- **`limit_set_id` figé sur chaque résultat** : un recalcul de limites ne réévalue jamais l'historique. La traçabilité est garantie.
- **`ciq_run`** matérialise la série analytique, indispensable pour `R-4s` et les règles inter-niveaux.
- Un résultat erroné n'est pas modifié mais **annulé** (`voided_at` + motif) puis ressaisi ; l'annulation est auditée.

### 4.6 Facturation et notifications

| Table | Portée | Champs |
|---|---|---|
| `subscription` | T | stripe_customer_id, stripe_subscription_id, plan (monthly, yearly), status (trialing, active, past_due, canceled, suspended), trial_ends_at, current_period_end, grace_until | unique (tenant_id) |
| `stripe_event` | G | stripe_event_id (unique), type, received_at, processed_at, payload (jsonb) |
| `notification` | T | user_id (nullable pour « tous les responsables »), kind, object_type, object_id, level, dedup_key, read_at, emailed_at | unique (tenant_id, dedup_key) : le cron peut tourner deux fois sans doublon |

### 4.7 V1.1 (schéma réservé, non développé en V1)
`transmission`, `transmission_recipient` (user_id ou team_id), `transmission_read_receipt`, `transmission_comment`, `non_conformity` (numéro unique (tenant_id, year, seq)), `non_conformity_status_change`. Elles réutilisent `attachment`, `corrective_action` et `audit_event` sans modification.

## 5. Moteur de règles CIQ

Spécification détaillée à écrire dans `docs/regles-ciq.md` à l'étape 3. Principes retenus :

- **Entrée** : le point à évaluer + l'historique ordonné des points du même paramètre (tous niveaux), chacun avec son z-score calculé sur **son propre** jeu de limites. **Sortie** : liste des règles déclenchées avec leur gravité.
- **Limites strictes** : une règle « au-delà de 2s » se déclenche si |z| > 2. Une valeur exactement à 2s ne la déclenche pas. Les z-scores sont calculés en `Decimal` pour éviter les effets d'arrondi.
- **Changement de lot** : les séquences inter-séries sont réinitialisées par défaut (option par paramètre pour les enchaîner via le z-score).
- **Valeurs manquantes ou annulées** : ignorées ; elles ne cassent pas une séquence.

| Règle | Portée | Défaut |
|---|---|---|
| 1-2s | un point, \|z\| > 2 | avertissement |
| 1-3s | un point, \|z\| > 3 | rejet |
| 2-2s | 2 points consécutifs du même niveau, ou 2 niveaux de la même série, > 2 du même côté | rejet |
| R-4s | même série uniquement : un niveau > +2, un autre < −2 | rejet |
| 4-1s | 4 points consécutifs > 1 du même côté (même niveau ou inter-niveaux) | rejet (configurable en avertissement) |
| 10x | 10 points consécutifs du même côté de la moyenne (même niveau ou inter-niveaux) | rejet (configurable en avertissement) |
| Shewhart A | un point hors ±3s | rejet |
| Shewhart B | 2 points sur 3 consécutifs hors ±2s du même côté | rejet |
| Shewhart C | 7 points consécutifs du même côté | avertissement |
| Shewhart D | 10 points sur 11 du même côté | avertissement |

Si l'historique est trop court, la règle n'est pas évaluée (et non « passée »). Ce cas est signalé dans l'évaluation.

## 6. Permissions

Permissions nommées (`ciq.result.create`, `ciq.config.edit`, `metrology.plan.edit`, `corrective_action.validate`, `audit.view`, `users.manage`, `billing.manage`, `export.run`…), et une table statique `ROLE_PERMISSIONS` en code (pas en base : 4 rôles fixes en V1).

- Décorateur `@require("ciq.result.create")` sur les routes.
- `can("…")` dans les templates, pour masquer les boutons. Le masquage est une commodité ; **la vérification serveur fait foi**.
- Contrôle d'abonnement : si le labo est en `read_only`, toute permission d'écriture est refusée globalement (sauf `billing.manage`).
- Garde-fous : un admin ne peut pas retirer son propre rôle s'il est le dernier admin ; le changement de rôle est audité.

## 7. Audit

- `audit_service.record(action, obj, before, after, reason)` est appelé dans les services, **dans la même transaction** que la modification : pas d'écriture métier sans audit, ni l'inverse.
- Les instantanés `before` / `after` sont produits par une sérialisation avec liste d'exclusion (password_hash, token_hash, champs `*_secret`).
- Protection en base :
  - le rôle PostgreSQL applicatif n'a que `INSERT, SELECT` sur `audit_event` ;
  - un trigger `BEFORE UPDATE OR DELETE` lève une exception (défense si les droits sont mal configurés) ;
  - migrations exécutées avec un rôle propriétaire distinct du rôle applicatif.
- Consultation : liste filtrable et paginée, export CSV, réservée à `audit.view`.

## 8. Pièces jointes

- Upload : taille max (défaut 15 Mo), liste blanche (PDF, PNG, JPEG, DOCX, XLSX, CSV). Type détecté sur le contenu (octets magiques), pas sur l'extension ; SHA-256 calculé.
- Stockage sous `{tenant_id}/{uuid}` : le nom d'origine n'est jamais utilisé comme chemin.
- Téléchargement uniquement via `/fichiers/<uuid>`. Cette route contrôle le tenant (filtre ORM), la permission sur l'objet propriétaire et audite l'accès. Réponse `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`. En S3 : URL présignée de 60 s, générée après contrôle.
- Interface `FileStorage` avec `LocalStorage` (dev) et `S3Storage` (prod), sélectionnée par variable d'environnement.

## 9. Principales menaces et parades

| Menace | Parade |
|---|---|
| Accès inter-labos (IDOR) | filtre ORM auto + FK composites + 404 + tests |
| Élévation de privilèges | rôle relu à chaque requête, permissions serveur, dernier admin protégé |
| Force brute | compteur d'échecs en base (email + IP), délai progressif, audit |
| Vol de session / fixation | régénération à la connexion, cookies sécurisés, `session_version` |
| CSRF | Flask-WTF sur tous les POST, y compris HTMX (en-tête `X-CSRFToken`) |
| XSS | autoescape Jinja, pas de `|safe` sur les données utilisateur, CSP stricte sans inline |
| Fichier malveillant | liste blanche, détection du type, stockage hors web, `nosniff`, téléchargement forcé |
| Injection de formules CSV | préfixe `'` sur les cellules commençant par `= + - @` (et tabulation, retour chariot) |
| Webhook Stripe falsifié ou rejoué | vérification de signature, table `stripe_event` unique, traitement idempotent |
| Falsification de l'audit | INSERT/SELECT seulement + trigger + rôle de migration séparé |
| Fuite de secrets | variables d'environnement, `.env` ignoré, secrets absents des logs et de l'audit |
| Jetons email interceptés | stockage du hash seulement, expiration courte, usage unique |

## 10. Dépendances

Runtime : `Flask`, `Flask-SQLAlchemy` (compatible SQLAlchemy 2), `SQLAlchemy`, `Flask-Migrate`, `Flask-Login`, `Flask-WTF`, `psycopg[binary]` (v3), `argon2-cffi`, `stripe`, `WeasyPrint` (PDF à partir de gabarits HTML/Jinja), `python-magic` (détection de type), `boto3` (uniquement si S3), `gunicorn`, `python-dotenv` (dev).

Tests : `pytest`, `pytest-cov`, `factory-boy`.

Front (vendorisé dans `static/vendor`) : `htmx`, `chart.js`.

Exclus volontairement : Redis, Celery, Flask-Limiter (son stockage mémoire n'est pas partagé entre workers), frameworks JS, ORM additionnels.

Point d'attention : WeasyPrint nécessite Pango dans l'image Docker (quelques Mo de paquets système). L'alternative ReportLab n'a aucune dépendance système, mais les rapports doivent alors être construits en code et non en HTML. **Proposition : WeasyPrint.**

## 11. Décisions à valider avant l'étape 2

1. **Nom du projet / package** : `labqualite` provisoire.
2. **Utilisateur multi-labos** avec sélecteur de laboratoire actif : retenu.
3. **Forfait par laboratoire**, plafond d'utilisateurs actifs configurable (15 par défaut) : à confirmer, ainsi que les prix mensuel et annuel.
4. **RLS PostgreSQL** non activée en V1 (schéma compatible) : à confirmer.
5. **Échéance métrologique** recalculée depuis la date effective de réalisation : à confirmer (sinon, depuis la date prévue).
6. **Changement de lot** : réinitialisation des séquences Westgard par défaut : à confirmer.
7. **Gravité par défaut** de `4-1s` et `10x` : rejet (Westgard classique) ou avertissement (pratique courante en labo) ?
8. **WeasyPrint** pour les PDF : à confirmer.
9. **Création de laboratoire** en libre-service (inscription + essai 30 j) ou uniquement par toi (admin plateforme) au début ? Proposition : libre-service désactivable par variable d'environnement, désactivé au lancement.
10. **Hébergeur** visé (Scaleway, OVHcloud, Clever Cloud…) : il conditionne le stockage S3 et la stratégie de sauvegarde PostgreSQL documentée à l'étape 7.
