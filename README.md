# Matrice Centreon → Zabbix

Outil d'inventaire de la configuration **Centreon** (hôtes, services, templates d'hôte,
templates de service, commandes) qui produit une **matrice de décision Excel** pour préparer
une migration vers **Zabbix** : quels templates et quels services reprendre, avec quel
équivalent Zabbix, et dans quel ordre.

## Ce que produit l'outil

Un classeur `.xlsx` (et en option des CSV) avec les onglets suivants :

| Onglet | Contenu |
|---|---|
| **Synthèse** | Volumétrie (hôtes, services, templates utilisés / total, collecteurs), répartition des propositions, top 15 des templates |
| **Matrice templates services** | **Unité de décision principale** : une ligne par template de service (et par commande pour les services sans template) : chaîne d'héritage, commande, plugin/mode Centreon Plugins, nombre de services / hôtes, catégorie, template ou item Zabbix suggéré, stratégie, effort, priorité, **proposition**, et colonnes à remplir : *Décision finale* (liste déroulante), *Template / item Zabbix cible*, *Commentaire* |
| **Matrice templates hôtes** | Même principe pour les templates d'hôte → templates Zabbix (nombre d'hôtes en direct ou par héritage, templates de service liés) |
| **Hôtes** | Inventaire des hôtes : adresse, collecteur (→ proxy Zabbix), groupes d'hôtes, templates, nombre de services, interface Zabbix suggérée (Agent / SNMP / JMX), templates Zabbix suggérés |
| **Services** | Inventaire détaillé hôte par hôte avec la ligne de commande résolue (macros et `$ARGn$` remplacés). La colonne *Décision (template)* **reprend automatiquement** la décision prise sur le template, *Décision (service)* permet une exception, *Décision effective* donne le résultat final |
| **Croisé hôtes x tpl hôtes** | Matrice hôtes × templates d'hôte (D = direct, h = hérité) |
| **Croisé hôtes x tpl services** | Matrice hôtes × templates de service (nombre de services) |
| **Commandes** | Commandes Centreon, exécutable, plugin, mode, nombre de services qui les utilisent |

### Logique de proposition

| Situation | Proposition |
|---|---|
| Template sans aucun service / hôte | Ne pas migrer (inutilisé) |
| Template uniquement utilisé comme parent | Hérité (pas d'action directe) |
| Tous les services désactivés (ou hôtes désactivés) | Ne pas migrer |
| Couvert par un template officiel Zabbix | Migrer (template natif) — effort faible |
| Équivalent Zabbix à paramétrer (clé d'item, check simple, LLD) | Migrer (adapter) — effort moyen |
| Check spécifique (script maison) | À analyser — UserParameter / script externe / plugin agent 2 |
| Sans objet sous Zabbix (dummy, passif…) | Ne pas migrer |

La **priorité** dépend du nombre d'hôtes concernés (Haute ≥ 20, Moyenne ≥ 5, paramétrable).

Les correspondances sont définies dans [`mapping/zabbix_mapping.yaml`](mapping/zabbix_mapping.yaml)
(regex sur le nom de template, la commande, le plugin / mode Centreon Plugins ou la ligne de
commande). **Adaptez ce fichier à votre parc** (templates maison, nomenclature) puis régénérez la matrice.

## Installation

```bash
python3 -m pip install -r requirements.txt
```

Python ≥ 3.9. Dépendances : `openpyxl`, `PyYAML`, et `PyMySQL` (uniquement pour la lecture en base).

## Récupérer les données Centreon

Deux sources au choix ; la base SQL est la plus complète, l'export CLAPI ne demande aucun accès réseau supplémentaire.

### Option 1 — Export CLAPI (sur le serveur central)

```bash
centreon -u admin -p 'MOT_DE_PASSE' -e > centreon_export.txt
# ou limité aux objets utiles :
centreon -u admin -p 'MOT_DE_PASSE' -e --select='HOST' --select='HTPL' --select='STPL' \
    --select='CMD' --select='HG' --select='HGSERVICE' > centreon_export.txt
```

Copier le fichier sur le poste d'analyse, puis :

```bash
python3 -m centreon_inventory clapi centreon_export.txt -o matrice_centreon_zabbix.xlsx
```

### Option 2 — Lecture directe de la base `centreon` (MariaDB/MySQL)

Créer un compte en lecture seule :

```sql
CREATE USER 'inventaire'@'%' IDENTIFIED BY '...';
GRANT SELECT ON centreon.* TO 'inventaire'@'%';
```

Puis :

```bash
export CENTREON_DB_PASSWORD='...'
python3 -m centreon_inventory db --db-host centreon-db.example.local --db-user inventaire \
    -o matrice_centreon_zabbix.xlsx
```

### Options communes

| Option | Rôle |
|---|---|
| `-o / --output` | Fichier Excel produit |
| `--mapping` | Référentiel de correspondance YAML alternatif |
| `--csv-dir DIR` | Exporte aussi chaque tableau en CSV (`;`, UTF-8) |
| `--json FICHIER` | Exporte l'inventaire normalisé brut en JSON (réutilisable pour générer un import Zabbix) |
| `--seuil-haute N` / `--seuil-moyenne N` | Seuils de priorité en nombre d'hôtes (20 / 5) |
| `--no-mask` | Ne pas masquer les secrets dans les lignes de commande |

## Exemple

Un export fictif est fourni pour tester :

```bash
python3 -m centreon_inventory clapi examples/centreon_export_exemple.txt -o examples/matrice_exemple.xlsx
```

Le résultat est disponible dans [`examples/matrice_exemple.xlsx`](examples/matrice_exemple.xlsx).

## Méthode de travail conseillée

1. Générer la matrice, parcourir l'onglet **Synthèse**.
2. Dans **Matrice templates services**, trier par *Nb services* / *Priorité* et valider la
   *Décision finale* de chaque template (les cellules jaunes sont à remplir).
3. Faire de même dans **Matrice templates hôtes** (choix des templates Zabbix cibles).
4. Filtrer l'onglet **Services** sur *Décision effective* pour obtenir la liste exacte des
   services à migrer et traiter les exceptions dans *Décision (service)*.
5. Utiliser l'onglet **Hôtes** (interface, proxy, groupes) pour préparer l'import des hôtes dans Zabbix.

## Sécurité

- Les macros marquées « mot de passe » dans Centreon sont masquées dès la lecture.
- Les secrets visibles dans les lignes de commande (`--password`, `--snmp-community`, `-C`, `--token`…) sont masqués.
- Les **arguments** de commande sont affichés tels quels : relisez le classeur avant de le diffuser.

## Tests

```bash
python3 -m pip install pytest
python3 -m pytest
```
