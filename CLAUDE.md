# Instructions permanentes

- La spécification complète du projet est dans `SPEC.md`. Lis-la avant toute étape.
- L'architecture validée est dans `docs/ETAPE-1-architecture.md`. Respecte-la ; si tu dois t'en écarter, dis-le et justifie avant de coder.
- Applique strictement la méthode de travail de la section 19 de `SPEC.md` : une seule étape à la fois, puis arrêt.
- Quand je tape « étape N », réalise uniquement l'étape N de la section 20.
- Code, commentaires utiles, interface et messages en français ; identifiants de code en anglais.
- Toute requête sur une donnée de laboratoire passe par le mécanisme multi-tenant centralisé. Jamais de `session.get(Model, id)` direct sur un modèle tenant-scoped.
- Toute écriture sur une donnée qualité produit un événement d'audit.
- Lance `pytest` avant de conclure une étape et donne le résultat.
- Aucun secret dans le dépôt, aucun `TODO` à la place d'une fonctionnalité demandée.
