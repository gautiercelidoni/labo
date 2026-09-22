# Moteur de règles CIQ — spécification

Code : `app/domain/ciq_rules/` (fonctions pures, sans Flask ni base de données).
Tests : `tests/domain/test_westgard.py`, `tests/domain/test_shewhart.py`.

## 1. Données d'entrée

Chaque résultat est converti en **point** :

| Champ | Signification |
|---|---|
| `value` | valeur mesurée (`Decimal`, jamais `float`) |
| `mean`, `sd` | cible (ou moyenne) et écart-type du **jeu de limites en vigueur au moment de la saisie** de ce point |
| `z` | `(value − mean) / sd`, calculé en `Decimal` exact |
| niveau, lot, jeu de limites, série | pour construire les séquences |
| mode | `westgard` ou `shewhart` |
| annulé | les points annulés sont ignorés |

Le jeu de limites est **figé sur chaque résultat** (`limit_set_id`) : un recalcul de limites ne réévalue
jamais l'historique ; les z-scores antérieurs restent ceux calculés avec les limites d'origine.

### Historique examiné

Pour évaluer une nouvelle série, le service charge les **60 derniers résultats non annulés** du même
paramètre (tous niveaux) antérieurs ou simultanés à la série, plus les autres niveaux de la série en
cours. 60 points couvrent largement la règle la plus longue (10 points sur 11) même avec 3 à 5 niveaux.

## 2. Séquences

- **Séquence du niveau** : points antérieurs du même niveau et du même mode, par ordre chronologique
  (date de série, puis ordre des niveaux, puis ordre de saisie), terminée par le point évalué.
- **Séquence inter-niveaux** (Westgard uniquement) : tous les niveaux du même mode, dans le même ordre.
- **Série analytique** (Westgard uniquement) : autres niveaux de la même série (`CIQRun`), quel que soit
  leur ordre de saisie.

### Changement de lot

Par défaut (décision validée) les séquences **repartent à zéro** :

- séquence du niveau : elle commence au premier point du lot courant ;
- séquence inter-niveaux : on remonte le temps et on s'arrête au premier changement de lot rencontré
  sur **n'importe quel** niveau ; la série contenant l'ancien lot est entièrement exclue.

Option par paramètre `chain_lots` : les séquences sont enchaînées malgré le changement de lot, ce qui
reste cohérent car chaque point est exprimé en z-score avec ses propres limites.

### Changement de jeu de limites (même lot)

Les séquences **ne sont pas** réinitialisées : chaque point est comparé à ses propres limites.

### Valeurs manquantes ou annulées

- Un résultat annulé est ignoré ; il ne casse pas une séquence (le point précédent et le suivant restent
  consécutifs).
- Un niveau non passé dans une série n'est pas une erreur : les règles intra-série (R-4s, 2-2s
  inter-niveaux) utilisent les niveaux présents.

## 3. Limites strictes

Toutes les comparaisons sont **strictes** :

| Condition | Déclenchée si |
|---|---|
| « au-delà de 2s » | `|z| > 2` |
| « au-delà de 3s » | `|z| > 3` |
| « au-delà de 1s » | `|z| > 1` |
| « du même côté de la moyenne » | `z > 0` ou `z < 0` ; **z = 0 casse la séquence** |

Une valeur exactement égale à cible + 2s donne `z = 2` et **ne déclenche pas** 1-2s. Le calcul décimal
exact évite les erreurs d'arrondi (ex. 5,5 + 2 × 0,3 = 6,1 donne exactement z = 2).

## 4. Règles de Westgard

| Règle | Définition implémentée | Gravité par défaut |
|---|---|---|
| 1-2s | le point évalué : `|z| > 2` | avertissement |
| 1-3s | le point évalué : `|z| > 3` | rejet |
| 2-2s | le point et le **point précédent du même niveau** > 2s du même côté, **ou** le point et un **autre niveau de la même série** > 2s du même côté | rejet |
| R-4s | même série : le point > +2s et un autre niveau < −2s (ou l'inverse) | rejet |
| 4-1s | les 4 derniers points (point évalué inclus) > 1s du même côté, dans la séquence du niveau **ou** dans la séquence inter-niveaux | **avertissement** (configurable en rejet) |
| 10x | les 10 derniers points du même côté de la moyenne, séquence du niveau **ou** inter-niveaux | **avertissement** (configurable en rejet) |

Décision validée à l'étape 1 : `4-1s` et `10x` en avertissement par défaut (pratique courante),
chaque règle étant réglable par paramètre : désactivée, avertissement ou rejet.

## 5. Cartes de contrôle de Shewhart (ISO 7870-2, Nordtest TR 569)

Limites de surveillance ±2s, limites d'action ±3s. Règles évaluées sur la séquence du niveau.

| Règle | Définition | Gravité par défaut |
|---|---|---|
| A | un point hors des limites d'action : `|z| > 3` | rejet |
| B | 2 points sur les 3 derniers (point évalué inclus, lui-même concerné) hors ±2s **du même côté** | rejet |
| C | 7 points consécutifs du même côté de la moyenne | avertissement |
| D | 10 points sur les 11 derniers du même côté de la moyenne (point évalué inclus et concerné) | avertissement |

### Période de référence

- Moyenne et écart-type **d'échantillon (n − 1)** calculés en `Decimal` sur les résultats non annulés
  du lot entre deux dates choisies par le laboratoire.
- Minimum configurable par paramètre (20 par défaut) : en dessous, le calcul est **refusé sauf
  confirmation explicite**, et un avertissement est affiché.
- Recalcul **toujours manuel**, avec motif obligatoire ; l'ancien jeu est clôturé (`valid_to`) et
  conservé dans l'historique ; un seul jeu actif par lot (index unique partiel en base).

## 6. Évaluation « non réalisée »

Une règle qui n'a pas pu être évaluée faute d'historique n'est pas considérée comme « passée » : elle
figure dans `rules_not_evaluated` avec son motif (« moins de 4 points disponibles », « un seul niveau
dans la série »…). Une règle n'est marquée non évaluée que si **plus d'historique aurait pu la
déclencher** : si le point évalué ne remplit pas lui-même la condition (ex. |z| ≤ 2 pour 2-2s), la règle
est évaluée et négative.

## 7. Statut

- `accepted` : aucune règle déclenchée ;
- `warning` (alerte) : au moins une règle en avertissement, aucune en rejet ;
- `rejected` : au moins une règle en rejet.

Le statut d'une série est « rejetée » si un de ses résultats valides est rejeté. Une série rejetée ne
peut être validée définitivement qu'avec une **justification** et une **action corrective** (créée ou
existante) : elle passe alors au statut « rejet traité ». Commentaire obligatoire en cas d'alerte et/ou de
rejet selon la configuration du paramètre.
