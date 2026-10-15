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

Python ≥ 3.9. Dépendances : `requests`, `openpyxl`, `PyYAML` (et `PyMySQL` uniquement pour la lecture directe en base).

## Récupérer les données Centreon par l'API (méthode recommandée)

```bash
export CENTREON_API_PASSWORD='...'
python3 -m centreon_inventory api \
    --url https://centreon.example.com/centreon \
    --user inventaire \
    -o matrice_centreon_zabbix.xlsx
```

L'outil s'exécute depuis n'importe quel poste ayant accès en HTTPS à l'interface Centreon ;
rien n'est installé sur le serveur Centreon.

### Prérequis côté Centreon

- Un compte Centreon dédié (ex. `inventaire`) avec, dans *Configuration > Utilisateurs >
  Contacts / Utilisateurs > onglet Authentification Centreon* :
  - **Reach API Configuration** = Oui (API v1) ;
  - un accès en lecture aux menus de configuration hôtes / services (profil ACL ou compte
    administrateur) pour l'API v2.
- Le compte doit voir tous les hôtes à inventorier (les ACL Centreon filtrent ce que l'API renvoie).

### Ce que fait l'extraction

| Donnée | API v2 (Centreon ≥ 23.10, utilisée automatiquement) | API v1 (toutes versions, repli) |
|---|---|---|
| Commandes | — | `CMD show` |
| Templates d'hôte et héritage | — | `HTPL show`, `HTPL gettemplate` |
| Templates de service (parent, liens, macros) | `GET /configuration/services/templates` | `STPL show`, `STPL getparam template`, `STPL gethosttemplate`, `STPL getmacro` |
| Hôtes (templates, collecteur, groupes) | `GET /configuration/hosts` | `HOST show`, `HOST gettemplate`, `INSTANCE gethosts`, `HG getmember` |
| Services (commande, arguments, état) | — | `SERVICE show` |
| Template de chaque service | `GET /configuration/services` | `HGSERVICE getparam <id>;service_template_model_stm_id` |
| Macros hôtes / services (héritées comprises) | — | `HOST getmacro`, `SERVICE getmacro` |
| Services de groupes d'hôtes | — | `HGSERVICE show`, `getparam`, `getmacro` |

- **Lecture seule** : seules des actions `show` / `get*` et des `GET` sont appelées.
- `SERVICE getparam` n'est **jamais** utilisé par défaut : dans Centreon, cet appel marque les
  collecteurs « configuration modifiée » (sans rien changer réellement). Il n'est proposé qu'en
  dernier recours, avec `--autoriser-getparam`, si ni l'API v2 ni `HGSERVICE getparam` ne répondent.
- Les appels objet par objet sont parallélisés (`--workers`, 8 par défaut). Sur un gros parc,
  `--sans-macros` réduit fortement le nombre d'appels (les lignes de commande seront moins résolues,
  la correspondance Zabbix reste calculée).
- Les macros de type mot de passe sont masquées dès la lecture.

### Options spécifiques à l'API

| Option | Rôle |
|---|---|
| `--url` | URL de Centreon (avec le chemin `/centreon`) |
| `--user` / `--password` | Identifiants (mot de passe : sinon `CENTREON_API_PASSWORD`, sinon saisie masquée) |
| `--insecure` / `--ca-bundle FICHIER` | Certificat auto-signé : désactiver la vérification ou fournir l'AC |
| `--api-v1-seule` | Ne pas utiliser l'API v2 |
| `--sans-macros` | Ne pas lire les macros (plus rapide) |
| `--workers N` / `--timeout S` | Parallélisme et timeout HTTP |
| `--autoriser-getparam` | Dernier recours pour lire le template des services (voir ci-dessus) |

## Autres sources possibles

### Export CLAPI (sur le serveur central)

```bash
centreon -u admin -p 'MOT_DE_PASSE' -e > centreon_export.txt
python3 -m centreon_inventory clapi centreon_export.txt -o matrice_centreon_zabbix.xlsx
```

### Lecture directe de la base `centreon` (MariaDB/MySQL, compte en lecture seule)

```bash
export CENTREON_DB_PASSWORD='...'
python3 -m centreon_inventory db --db-host centreon-db.example.local --db-user inventaire \
    -o matrice_centreon_zabbix.xlsx
```

### Options communes (toutes sources)

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

Les tests de l'extraction API utilisent un faux serveur Centreon (`tests/fake_centreon.py`)
qui reproduit le format des réponses de l'API v1 (CLAPI) et de l'API v2, établi d'après le code
source de Centreon. Faites un premier essai sur votre instance et comparez les volumes
(nombre d'hôtes / services) avec l'interface.
