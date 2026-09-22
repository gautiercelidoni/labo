# SPEC — SaaS de gestion qualité pour petits laboratoires ISO/IEC 17025

Tu es un architecte logiciel et développeur full-stack senior spécialisé en Python, Flask, applications SaaS B2B, sécurité des données et logiciels destinés aux laboratoires.

Ta mission : concevoir puis développer le MVP complet et exécutable d'une application web SaaS de gestion de la qualité destinée aux petits laboratoires accrédités ou engagés dans une démarche ISO/IEC 17025 (vétérinaires, environnementaux, agroalimentaires, analyse des eaux). Chaque laboratoire compte environ 2 à 15 utilisateurs.

L'application doit être assez propre, sécurisée et structurée pour servir de base à un vrai produit commercial. Ce n'est pas une démonstration visuelle.

# 1. Contexte

Je suis technicien de laboratoire sur plateau automatisé (Roche cobas, ISO 15189 / COFRAC) et je développe des outils internes. J'ai déjà créé localement trois outils HTML : synthèse CIQ, optimiseur de calibrations, cahier de transmission. Je veux les réunir et les faire évoluer dans un SaaS multi-laboratoires avec abonnement.

Utilisateurs finaux : techniciens, responsables qualité, responsables techniques, administrateurs de laboratoire. Ils ne sont généralement pas informaticiens.

L'interface doit être : entièrement en français, simple, rapide, adaptée à un usage quotidien, responsive, lisible sur de vieux PC de laboratoire, utilisable au clavier, cohérente entre les modules.

L'application aide à structurer la gestion qualité. Elle ne doit jamais prétendre garantir à elle seule la conformité ou l'accréditation ISO/IEC 17025 ou COFRAC.

**Aucune donnée patient ni donnée de santé humaine n'est traitée : pas d'exigence HDS.** Ne pas surdimensionner le chiffrement ou l'hébergement pour ce motif.

# 2. Périmètre

## V1 (MVP commercialisable)

1. authentification et gestion des utilisateurs ;
2. architecture multi-tenant ;
3. contrôles qualité internes (Westgard + cartes de contrôle Shewhart) ;
4. métrologie et échéances ;
5. tableau de bord central ;
6. abonnement Stripe ;
7. exports PDF et CSV ;
8. journal d'audit non modifiable ;
9. tests automatisés essentiels ;
10. conteneurisation et documentation de déploiement.

## V1.1 (après les premiers retours clients)

11. cahier de transmission ;
12. gestion simplifiée des non-conformités.

Les modèles de données de la V1.1 sont conçus dès l'étape 1 pour ne pas casser l'architecture, mais leur développement n'intervient qu'après la V1. La V1 inclut uniquement une table `CorrectiveAction` minimale, liée aux rejets CIQ, que le module NC réutilisera.

# 3. Stack technique imposée

## Backend
Python 3.12, Flask (application factory), SQLAlchemy 2.x, Flask-Migrate / Alembic, PostgreSQL, blueprints, validation serveur systématique, pytest.

## Frontend
Jinja2, HTMX quand il simplifie réellement l'interface, JavaScript vanilla pour le reste, HTML sémantique, CSS sobre et léger, aucun framework frontend lourd. Graphiques avec Chart.js **servi en local** (pas de CDN : certains postes de labo ont un accès Internet filtré).

## Authentification
- connexion email + mot de passe ;
- hachage Argon2id ;
- réinitialisation par email avec jeton signé, expirant et à usage unique ;
- sessions sécurisées, protection CSRF ;
- limitation des tentatives de connexion **sans Redis** (compteur en base) ;
- cookies `Secure`, `HttpOnly`, `SameSite` en production ;
- invalidation des autres sessions après changement de mot de passe.

**Un utilisateur peut appartenir à plusieurs laboratoires** (ex. consultant qualité externe), avec un rôle distinct par laboratoire. Après connexion, s'il a plusieurs appartenances, il choisit le laboratoire actif ; il peut en changer sans se reconnecter. Le laboratoire actif est stocké en session et revérifié à chaque requête.

## Paiement
- Stripe Checkout + Stripe Billing, portail client Stripe ;
- **modèle tarifaire : forfait par laboratoire**, mensuel ou annuel, plafond d'utilisateurs actifs configurable (défaut 15) ;
- identifiants de prix Stripe fournis par variables d'environnement (aucun montant codé en dur) ;
- essai gratuit de 30 jours ;
- webhooks vérifiés et idempotents ;
- aucune donnée bancaire stockée localement ;
- **facturation France** : TVA gérée par Stripe Tax si activé ; sinon, mention configurable de franchise en base (« TVA non applicable, art. 293 B du CGI ») sur les factures ; informations légales de l'émetteur (SIRET, adresse) configurables.

## Déploiement
Docker, Docker Compose (dev et installation simple), hébergement France/UE, configuration par variables d'environnement, Gunicorn, reverse proxy Nginx ou Traefik, stockage local des pièces jointes en dev et compatible S3 en production.

# 4. Principes d'architecture

## 4.1 Flask
Application factory. Blueprints : `auth`, `dashboard`, `admin`, `ciq`, `metrologie`, `billing`, `audit` (V1) ; `transmissions`, `non_conformites` (V1.1).

Services séparés : règles CIQ (Westgard et Shewhart), exports PDF/CSV, emails, notifications, fichiers, Stripe, audit. Pas de logique métier dans les routes.

## 4.2 Multi-tenant
Chaque laboratoire = un tenant. Toute donnée d'un laboratoire porte un `tenant_id` obligatoire, sauf tables réellement globales.

L'isolation ne doit pas reposer sur un filtre ajouté à la main dans chaque requête. Stratégie centralisée et testable :
- contexte de tenant lié à la session ;
- filtrage automatique des requêtes ORM par tenant ;
- contrôles d'autorisation systématiques avant lecture, modification, suppression ;
- contraintes de base de données empêchant les références croisées entre tenants ;
- tests prouvant qu'un utilisateur du labo A ne peut jamais consulter, modifier, télécharger ou supprimer une ressource du labo B, même en modifiant un identifiant dans l'URL.

Toute route recevant un identifiant vérifie l'appartenance au tenant courant **et** le rôle.

## 4.3 Rôles (par appartenance à un laboratoire)
- **Administrateur** : utilisateurs, rôles, configuration, abonnement, tous les modules, archivage selon règles.
- **Responsable qualité** : données qualité, configuration CIQ, validation des actions correctives, journal d'audit ; en V1.1 clôture des NC.
- **Technicien** : saisie CIQ, consultation équipements, réalisation d'événements de métrologie, actions qui lui sont attribuées ; en V1.1 transmissions et déclaration de NC.
- **Lecture seule** : consultation autorisée, aucune écriture.

Autorisations centralisées (décorateurs ou couche de permissions).

## 4.4 Journal d'audit
Append-only. Champs minimum : tenant, utilisateur, action, type d'objet, identifiant, horodatage UTC, IP si utile, ancienne valeur, nouvelle valeur, motif.

Actions tracées : création, modification, changement de statut, validation, clôture, archivage, connexion, échec de connexion, changement de laboratoire actif, export, téléchargement de pièce jointe, modification de droits.

Aucune modification ni suppression possible depuis l'application, **et** protection au niveau PostgreSQL. Instantanés JSON des champs modifiés, sans secrets, mots de passe ni jetons.

# 5. Modules fonctionnels

## 5.1 Contrôles qualité internes (V1)

### Configuration
Équipements, paramètres/analytes, unités, niveaux de contrôle, fabricants, lots (dates d'utilisation), mode d'évaluation par paramètre et par niveau, règles actives.

### Deux modes d'évaluation, choisis par paramètre et niveau

**Mode « Westgard »** (usage biologie) : cible et écart-type fournis (fournisseur ou labo). Règles configurables : `1-2s`, `1-3s`, `2-2s`, `R-4s`, `4-1s`, `10x`.

**Mode « carte de contrôle Shewhart »** (usage 17025 : eaux, environnement, agro — ISO 7870-2, guide Nordtest TR 569) :
- moyenne et écart-type calculés sur une **période de référence** choisie par le labo (minimum 20 valeurs, configurable) ;
- limites de surveillance ±2s, limites d'action ±3s ;
- règles configurables : un point hors limites d'action ; 2 points consécutifs sur 3 hors limites de surveillance du même côté ; 7 points consécutifs du même côté de la moyenne ; 10 points sur 11 du même côté ;
- recalcul manuel des limites (jamais automatique), avec motif obligatoire et historique des jeux de limites ; les résultats restent évalués avec les limites en vigueur au moment de la saisie ;
- avertissement si la période de référence contient moins de valeurs que le minimum.

### Saisie
Série analytique (plusieurs niveaux passés ensemble) ou résultat isolé : date et heure, équipement, paramètre, niveau, lot, valeur, technicien, commentaire (obligatoire en cas d'alerte ou de rejet selon configuration).

Calculs automatiques : écart à la cible, z-score, statut (accepté / alerte / rejet), règles déclenchées.

### Graphiques
Levey-Jennings (mode Westgard) ou carte de contrôle (mode Shewhart) : cible/moyenne, ±1s, ±2s, ±3s, points chronologiques, marquage des alertes et rejets, changements de lot et de jeu de limites, infobulle détaillée.

### Moteur de règles
Fonctions pures, sans accès base, testables. Documenter précisément : nombre de résultats historiques examinés, gestion multi-niveaux (intra-série et inter-séries), distinction avertissement/rejet, comportement sur les valeurs exactement aux limites, données manquantes, changement de lot, changement de jeu de limites.

### Rejet
Commentaire obligatoire ; action corrective créée ou renseignée ; pas de validation définitive sans justification ; date, auteur et action tracés dans l'audit.

### Consultation et exports
Historique filtrable (date, équipement, paramètre, lot, niveau, statut, mode), export CSV, rapport PDF mensuel par paramètre, rapport synthétique par équipement, impression propre.

## 5.2 Métrologie, étalonnages et maintenances (V1)

Parc d'équipements : nom, catégorie, fabricant, modèle, n° de série, identifiant interne, localisation, mise en service, statut, criticité, responsable, notes, pièces jointes.
Statuts : en service, hors service, en maintenance, suspendu, réformé.

Types d'événements : étalonnage, vérification, maintenance préventive, maintenance corrective, contrôle intermédiaire, qualification.

Chaque plan d'événement : périodicité, dernière réalisation, prochaine échéance, prestataire, responsable interne, statut, commentaire. Chaque réalisation : date effective, résultat (conforme / non conforme), certificat ou compte rendu en pièce jointe.

Un étalonnage non conforme propose de passer l'équipement en « suspendu ».

Alertes à J-30, J-7, J0 et en retard. Vue liste, vue calendrier, filtres, fiche historique par équipement, indicateur de retard, commande de recalcul des échéances, commande cron de génération des notifications.

## 5.3 Cahier de transmission (V1.1)

Champs : auteur, destinataire (personne, équipe ou poste), catégorie, priorité (normale, importante, urgente), titre, contenu, date, échéance facultative, statut (nouveau, lu, en cours, traité, archivé), accusé de lecture horodaté, pièces jointes.

Vues reçues/envoyées, filtres, commentaires de suivi, historique, badge non lus sur le tableau de bord, aucune suppression d'une transmission déjà lue (archivage).

## 5.4 Non-conformités (V1.1)

Champs : numéro automatique par labo et par année, titre, description, date de détection, détecteur, origine, gravité, impact, mesure immédiate, analyse de cause, action corrective, responsable, date cible, vérification d'efficacité, date de clôture, validateur, statut, pièces jointes.

Statuts : brouillon, ouverte, en analyse, action en cours, en vérification, clôturée, annulée (justification obligatoire).

Clôture : analyse de cause + action corrective (ou justification de non-action) + responsable qualité + vérification d'efficacité (ou justification). Historique complet des statuts. Réutilise la table `CorrectiveAction` de la V1 ; un rejet CIQ pourra être converti en NC.

# 6. Tableau de bord

Par laboratoire : CIQ en alerte, CIQ rejetés non traités, échéances métrologiques à venir, échéances dépassées, actions correctives en retard, activité récente ; en V1.1 transmissions non lues/urgentes et NC ouvertes. Cartes cliquables vers des listes pré-filtrées. Affichage adapté au rôle.

# 7. Administration du laboratoire

Informations du labo, logo, fuseau horaire, utilisateurs, invitations (email + jeton à durée limitée), rôles, équipes/postes, catégories, paramètres, notifications, abonnement, conservation des données, exports administratifs.

Aucune inscription libre dans un laboratoire existant sans invitation. La création d'un nouveau laboratoire (inscription + essai) crée son premier administrateur.

# 8. Pièces jointes

Certificats, comptes rendus, (V1.1) NC et transmissions. Taille max configurable, liste blanche de formats, nom de stockage interne aléatoire, jamais le nom fourni, contrôle du tenant au téléchargement, stockage hors dossier public, protection contre la traversée de répertoires, métadonnées en base, S3 possible en production. Aucun fichier accessible d'un labo à l'autre, même avec une URL devinée.

# 9. Stripe et cycle d'abonnement

États : essai, actif, paiement en retard, résilié, suspendu.
Checkout mensuel et annuel, essai 30 jours, portail client, webhooks idempotents, journalisation des événements utiles, synchronisation du statut, lecture seule après expiration (données jamais supprimées immédiatement), période de grâce configurable, contrôle du plafond d'utilisateurs actifs.

L'état réel provient uniquement des webhooks, jamais du retour navigateur.

# 10. RGPD et sécurité

Collecte minimale, conservation configurable, export des données d'un labo, suppression ou anonymisation contrôlée d'un utilisateur, journalisation des accès sensibles, CSRF, validation serveur, échappement des sorties, requêtes paramétrées, autorisations côté serveur, protection IDOR, limitation des tentatives, secrets hors Git, en-têtes HTTP de sécurité, HTTPS en production, sauvegardes documentées, dates en UTC affichées dans le fuseau du labo, **protection contre l'injection de formules dans les exports CSV**.

Configurations distinctes : développement, test, production.

# 11. Modèle de données

Proposer un modèle relationnel comprenant au minimum : Laboratory (tenant), User, Membership, Team, Invitation, LoginAttempt, AuditEvent, Equipment, MaintenancePlan, MaintenanceEvent, Attachment, CIQParameter, ControlLevel, ControlLot, CIQRuleConfig, ControlLimitSet (jeux de limites Shewhart/Westgard), CIQRun, CIQResult, CorrectiveAction, Subscription, StripeEvent, Notification ; et pour la V1.1 : Transmission, TransmissionRecipient, TransmissionReadReceipt, NonConformity, NonConformityStatusChange.

Noms adaptables si justifiés. Décrire relations, contraintes, index, règles de suppression, champs d'audit. Privilégier : UUID, contraintes uniques incluant le tenant, index sur colonnes filtrées, archivage plutôt que suppression pour les données qualité, dates de création/modification, non-nullité cohérente.

# 12. Données de démonstration

Commande Flask générant **deux** laboratoires fictifs (pour tester l'isolation) dont un complet : administrateur, responsable qualité, deux techniciens, un lecteur, un consultant membre des deux labos, équipements, paramètres en mode Westgard et en mode Shewhart, niveaux, lots, résultats normaux, résultats déclenchant chaque règle, échéances (à venir, J-7, en retard), actions correctives, abonnement de test. Identifiants affichés uniquement en développement.

# 13. Tests obligatoires (pytest)

**Multi-tenant** : lecture, modification, suppression, téléchargement et accès par UUID modifié impossibles entre labos ; listes, exports et tableaux de bord jamais mélangés ; contraintes uniques par tenant ; un membre de deux labos ne voit que le labo actif ; retrait d'une appartenance effectif immédiatement.

**Autorisations** : chaque rôle a uniquement ses droits ; lecture seule sans aucune écriture ; seul l'admin gère rôles et abonnement ; seul un rôle autorisé valide une action corrective.

**Règles CIQ** : chaque règle Westgard (`1-2s`, `1-3s`, `2-2s`, `R-4s`, `4-1s`, `10x`) et chaque règle Shewhart testées individuellement ; valeurs exactement aux limites ; changement de lot ; changement de jeu de limites ; résultats manquants ; séquences insuffisantes ; multi-niveaux ; remise à zéro des séquences ; période de référence insuffisante.

**Authentification et sécurité** : connexion valide/invalide, blocage après N échecs, jeton expiré, jeton déjà utilisé, CSRF, route sans permission, fichier d'un autre tenant, webhook Stripe à signature invalide, idempotence d'un webhook déjà traité, cellule CSV commençant par `=`, `+`, `-`, `@`.

# 14. Exports

PDF A4 propres : rapport CIQ mensuel, historique d'un équipement, planning métrologique (V1.1 : liste et fiche NC). Chaque rapport : nom et logo du labo, période, date de génération, auteur, filtres appliqués, pagination, identifiant de rapport.

CSV compatibles Excel français (UTF-8 avec BOM, séparateur point-virgule, virgule décimale), documentés.

# 15. Performance et simplicité

Utilisable sur un vieux PC : pages légères, peu de JS, pagination serveur, index adaptés, pas de N+1, HTMX quand pertinent, chargement différé des graphiques, dépendances limitées. Pas de Redis, Celery, Kafka, React ni microservices sans nécessité démontrée. Alertes planifiées via une commande Flask lancée par cron.

# 16. Qualité du code

Exécutable, cohérent, typé quand utile, commentaires seulement si nécessaires, structuré par domaine. Interdits : pseudo-code, fonctions vides, boutons factices, routes simulées, données codées en dur en production, secrets dans Git, `TODO` à la place d'une fonctionnalité demandée.

Application factory, configuration par environnement, services métier, migrations Alembic, gestion uniforme des erreurs, logs structurés, transactions avec rollback correct, conventions de nommage cohérentes.

# 17. Documentation

`README.md` : présentation, prérequis, installation locale, Docker Compose, lancement sans Docker, base, migrations, données de démo, tests, variables d'environnement, emails, Stripe (dont TVA et mentions légales), stockage fichiers, PostgreSQL, création du premier laboratoire, sauvegarde/restauration, production, limites connues.

Également : `.env.example`, `docker-compose.yml`, `Dockerfile`, configuration pytest, dépendances versionnées, commandes de démarrage, endpoint ou commande de santé.

# 18. Hors périmètre

Application mobile native, IA, connexion aux automates, HL7/ASTM, signature électronique qualifiée, système documentaire complet, audits fournisseurs, GED réglementaire, validation réglementaire formelle du logiciel, workflows très configurables, moteur BPM, microservices, calcul d'incertitude de mesure. Architecture évolutive mais pas surdimensionnée.

# 19. Méthode de travail

Développement par étapes, jamais tout le projet en une réponse. À chaque étape :
1. décisions prises ;
2. hypothèses retenues ;
3. arborescence concernée ;
4. contenu complet des fichiers créés ou modifiés ;
5. commandes à exécuter ;
6. tests de l'étape ;
7. vérification manuelle ;
8. limites restantes ;
9. arrêt avant l'étape suivante.

Ne pose une question que si elle est réellement bloquante ; sinon, annonce une hypothèse raisonnable et avance.

# 20. Ordre de réalisation

**V1**
1. Architecture et modèle de données (voir `docs/ETAPE-1-architecture.md`, à valider).
2. Socle : factory, configuration, PostgreSQL, modèles fondamentaux, migrations, auth, invitations, appartenances multiples, rôles, permissions, multi-tenant, audit, tests d'isolation.
3. CIQ : saisie, séries, moteur Westgard + Shewhart testé, graphiques, actions correctives.
4. Métrologie : parc, plans, réalisations, alertes, cron.
5. Tableau de bord et exports PDF/CSV.
6. Stripe : offres, essai, webhooks, portail, TVA/mentions, lecture seule après expiration.
7. Déploiement et sécurisation : Docker production, sauvegardes, logs, documentation, tests.

**V1.1**
8. Cahier de transmission.
9. Non-conformités.

# 21. Critères d'acceptation de la V1

Démarre avec Docker Compose ; migrations OK sur base vide ; données de démo générées ; connexion ; plusieurs labos coexistent avec isolation stricte ; un utilisateur multi-labos change de labo actif ; rôles appliqués côté serveur ; résultat CIQ saisi et évalué dans les deux modes ; toutes les règles testées ; Levey-Jennings et carte de contrôle affichés ; rejet exigeant justification et action corrective ; échéances métrologiques sur le tableau de bord ; événements importants dans l'audit ; audit non modifiable même en SQL par le rôle applicatif ; exports PDF et CSV OK ; Stripe fonctionnel en mode test ; pièces jointes isolées par tenant ; tests critiques verts ; README suffisant pour qu'un autre développeur installe le projet.
