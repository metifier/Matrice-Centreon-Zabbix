# Matrice Centreon → Zabbix

Outil d'inventaire de la configuration **Centreon** (hôtes, services, templates d'hôte,
templates de service, commandes) qui produit une **matrice de décision Excel** pour préparer
une migration vers **Zabbix** : quels templates et quels services reprendre, avec quel
équivalent Zabbix, et dans quel ordre.

## Sous Windows

1. Installer **Python 3.10 ou plus récent** depuis <https://www.python.org/downloads/windows/>
   en cochant **« Add python.exe to PATH »** (aucun droit administrateur n'est nécessaire si
   l'installation se fait « pour l'utilisateur courant »).
2. Récupérer le projet (bouton *Code > Download ZIP* sur GitHub, puis extraire le ZIP).
3. **Double-cliquer sur `demarrer_interface.bat`** : au premier lancement, il crée un
   environnement Python local (`.venv`), installe les dépendances, démarre l'interface et
   l'ouvre dans le navigateur (<http://127.0.0.1:8080>). Fermer la fenêtre arrête l'interface.

En ligne de commande (PowerShell), depuis le dossier du projet :

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1          # si refusé : Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install -r requirements.txt

# interface web
python -m centreon_inventory web --ouvrir

# matrice Excel directement, par l'API Centreon (+ comparatif Zabbix)
$env:CENTREON_API_PASSWORD = 'mot_de_passe'
$env:ZABBIX_API_TOKEN = 'jeton_zabbix'
python -m centreon_inventory api --url https://centreon.example.com/centreon --user inventaire `
    --client "MDPH 973" --client-hotes "Mdph-973" `
    --zabbix-url https://zabbix.example.com -o matrice.xlsx
```

Points spécifiques à Windows :

- **Proxy d'entreprise** pour l'installation des dépendances :
  `$env:HTTPS_PROXY = 'http://proxy.entreprise.local:8080'` (PowerShell) ou
  `set HTTPS_PROXY=...` (cmd) avant de lancer `demarrer_interface.bat`. Les appels vers Centreon
  et Zabbix passent aussi par ce proxy s'il est défini : ajoutez leurs noms dans `NO_PROXY`
  s'ils sont joignables directement (`$env:NO_PROXY = 'centreon.example.com,zabbix.example.com'`).
- **Certificats** : avec Python 3.10+, l'outil fait confiance au magasin de certificats de
  Windows (paquet `truststore`) ; un certificat Centreon/Zabbix signé par l'autorité interne de
  l'entreprise est donc accepté sans option. Sinon : `--ca-bundle fichier.pem` ou `--insecure`.
- Les fichiers produits (Excel, CSV en UTF-8 avec BOM, séparateur `;`) s'ouvrent directement
  dans Excel. Fermez le classeur dans Excel avant de le régénérer sous le même nom.
- Les tests sont exécutés automatiquement sous Windows et Linux (GitHub Actions) à chaque push.

## Démarrage rapide : l'interface web

```bash
python3 -m pip install -r requirements.txt
python3 -m centreon_inventory web            # http://127.0.0.1:8080
```

L'interface permet de :

1. **Lancer les extractions** (onglet *Sources & extractions*) : Centreon par API, Zabbix par API,
   ou import d'un export CLAPI. Un jeu de démonstration (Centreon + Zabbix fictifs) permet
   de découvrir l'outil sans connexion.
2. **Définir des clients** (onglet *Clients*) : un client = des chaînes de caractères que
   doivent contenir les noms d'hôtes et/ou de services (ex. `Mdph-973`). Les préfixes de
   nommage récurrents sont détectés et proposés automatiquement.
3. **Consulter** la matrice pour tout le parc ou un client : tableau de bord, templates de
   service / d'hôte, hôtes, services, comparatif Zabbix ; recherche, filtres par colonne, tri.
4. **Décider** directement dans les tableaux (*Décision finale*, cible Zabbix, commentaire,
   exception par service) : les décisions sont enregistrées et se propagent aux services.
   Les champs *Template Zabbix cible* proposent une **auto-complétion des templates Zabbix**
   (voir ci-dessous).
5. **Exporter en Excel** le périmètre affiché (bouton *Exporter Excel*), décisions comprises.

Les données sont conservées en JSON dans `./data` (`--data-dir`). **Les mots de passe et jetons
ne sont jamais enregistrés** : ils ne servent que le temps de l'extraction.

| Option de `web` | Rôle |
|---|---|
| `--host` / `--port` | Adresse et port d'écoute (127.0.0.1:8080 par défaut) |
| `--data-dir` | Dossier de stockage des inventaires, clients et décisions |
| `--mapping` | Référentiel de correspondance alternatif |

Pour exposer l'interface à d'autres postes (`--host 0.0.0.0`), protégez-la par mot de passe :
`WEB_USER=... WEB_PASSWORD=... python3 -m centreon_inventory web --host 0.0.0.0`
(authentification HTTP Basic ; placez-la derrière un reverse proxy HTTPS).

## Périmètre client (filtre sur le nommage)

Le client est identifié par le nommage des hôtes et des services :

| Champ | Effet |
|---|---|
| Hôtes contiennent | le nom (ou l'alias) de l'hôte contient l'une des chaînes (ex. `Mdph-973`) |
| Groupes d'hôtes | l'hôte appartient à un groupe d'hôtes Centreon correspondant (ex. `ctg-eple-973-*`) |
| Collecteur | l'hôte est supervisé par un collecteur (poller) correspondant |
| Services contiennent | la description du service contient l'une des chaînes |
| Exclure | écarte les hôtes ou services contenant l'une des chaînes |
| Mode | `et` : l'hôte ET le service doivent correspondre ; `ou` : l'un OU l'autre |

Les critères d'hôte renseignés (nom, groupe, collecteur) se cumulent. Un motif contenant `*` ou `?`
porte sur le nom entier (ex. `ctg-eple-973-*`, comme dans conformit-).
Plusieurs chaînes se séparent par des virgules ; la recherche ignore la casse ; le préfixe
`re:` active une expression régulière (ex. `re:^Mdph-97[34]-`). Dans une vue client, les
templates non utilisés par ce client sont masqués et la synthèse indique le périmètre.
En ligne de commande : `--client "MDPH 973" --client-hotes "Mdph-973"`, `--client-groupes "ctg-eple-973-*"`,
`--client-collecteurs "Poller-973"` (voir `--help`).

**Filtrer avant la collecte** (case « Limiter la collecte au client » ou `--collecte-client`) : le périmètre
est résolu dans Centreon avant la collecte détaillée — membres des groupes d'hôtes correspondants et hôtes des
collecteurs correspondants — puis seuls ces hôtes, leurs services et les templates qu'ils utilisent sont lus.
Le bouton « Lister les groupes et collecteurs » (onglet Sources) interroge Centreon en deux appels pour choisir
les groupes et collecteurs du client avant toute collecte.

## Comparatif avec l'existant Zabbix

L'inventaire Zabbix (API JSON-RPC, méthodes `*.get` uniquement, Zabbix 5.0 à 7.x) récupère
les hôtes (groupes, templates liés, interfaces, proxy, nombre d'items) et les templates.
Authentification par **jeton d'API** (recommandé : *Utilisateurs > Jetons d'API*, rôle en lecture)
ou par identifiant / mot de passe.

Chaque hôte Centreon est rapproché d'un hôte Zabbix par nom, nom visible ou alias (sans tenir
compte de la casse), puis par adresse IP. Le comparatif ajoute :

| Onglet / colonne | Contenu |
|---|---|
| **Comparatif Zabbix hôtes** | Statut (*Présent – templates OK*, *Présent – templates à lier*, *Absent de Zabbix*), mode de correspondance, groupes, proxy, templates liés, **templates à lier** |
| **Uniquement dans Zabbix** | Hôtes Zabbix sans équivalent Centreon (dans le périmètre client) |
| **Templates Zabbix** | Templates suggérés : disponibles dans Zabbix ou **à importer** |
| Services → *Statut Zabbix* | *Couvert dans Zabbix*, *Template à lier*, *À créer dans Zabbix*, *Hôte absent de Zabbix*, *Non migré (décision)* |

### Corrélation services Centreon ↔ items Zabbix (partir du standard)

Plutôt que de recopier chaque service Centreon, la démarche conseillée est de **lier d'abord les templates
standards de Zabbix, puis de n'ajouter que les spécificités**. L'inventaire Zabbix lit pour cela les items et
les prototypes d'items (LLD) de chaque template, y compris ceux de ses templates liés. Pour chaque service
Centreon, l'outil cherche dans le template Zabbix suggéré (à défaut, celui de l'hôte, ex. template constructeur
réseau) un item qui mesure la même chose, selon la catégorie du service (section `correlation` de
`mapping/zabbix_mapping.yaml`, modifiable) :

| Couverture standard | Signification |
|---|---|
| Couvert par un item standard | le template standard mesure déjà ce que fait le service (item cité) |
| Template standard sans item correspondant | template standard lié, mais il faut lui ajouter un item |
| Template standard à importer dans Zabbix | template officiel suggéré absent de votre Zabbix |
| Spécificité à créer | aucun standard : item / UserParameter / script à créer |

Onglet **Corrélation items** (et feuille Excel *Corrélation items Zabbix*) : par template de service, items
correspondants et **approche recommandée**. L'onglet *Templates Zabbix* indique aussi l'**apport du standard** :
nombre d'items du template qui n'ont pas d'équivalent dans Centreon.

### Fiche détaillée

Dans les tableaux, **cliquez sur le nom** d'un template de service, d'un template d'hôte, d'un hôte ou d'un
service : un panneau affiche la proposition et sa justification, un exemple de ligne de commande résolue, la
chaîne d'héritage Centreon (commande, arguments, macros de chaque niveau), les services et hôtes concernés, et
les items du template Zabbix standard (l'item correspondant au service est marqué ✔). La décision se saisit
directement dans le panneau.

### Décisions : hôtes et décisions groupées

- Un hôte marqué **« Ne pas migrer »** entraîne tous ses services en « Ne pas migrer » (interface et Excel,
  colonne *Décision (hôte)*).
- Dans les tableaux modifiables, cochez des lignes (ou « Sélectionner les N résultats filtrés »), choisissez le
  champ et la valeur, puis **« Appliquer à la sélection »** : décision finale, cible ou commentaire en une fois.

En ligne de commande : `--zabbix-url https://zabbix.example.com` avec `ZABBIX_API_TOKEN=...`
(ou `--zabbix-user` et `ZABBIX_PASSWORD`).

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
| **Comparatif Zabbix…** | Présents si l'inventaire Zabbix a été lu (voir ci-dessus) |

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

### Auto-complétion des templates Zabbix cibles

Dans les colonnes *Template Zabbix cible* (templates d'hôte) et *Template / item Zabbix cible* (templates de
service), ainsi que dans la décision groupée, une liste de templates Zabbix s'ouvre dès que le champ reçoit le
focus :

* **sans saisie**, les templates proposés sont déduits du **nom du template Centreon** de la ligne : `IDRAC` →
  *Dell iDRAC by SNMP*, `Act-Srv-Lnx` → *Linux by Zabbix agent*, `Act-MSSQL-Srv` → *MSSQL by Zabbix agent 2*,
  `generic-host` → *ICMP Ping*, `Act-Tomcat-JMX` → *Apache Tomcat by JMX*… (abréviations courantes : Lnx, Win,
  ESX, Printer, SNMP / agent / JMX pour privilégier la bonne variante) ; le template suggéré par le référentiel
  passe en tête ;
* **en tapant**, la liste est filtrée sur le nom (ex. `cisco ios`) ; ↑ ↓ puis Entrée (ou clic) pour choisir,
  la saisie libre reste possible (item, template maison) ;
* seuls les templates **officiels de la version de Zabbix server visée** sont proposés, plus ceux déjà présents
  dans le Zabbix inventorié (badge *dans Zabbix*, ou *personnalisé* s'il ne s'agit pas d'un template officiel).

La version visée se choisit dans *Sources & extractions* → carte Zabbix (*Version de Zabbix server visée*) ;
par défaut, celle du Zabbix inventorié. Une version sans catalogue propre utilise la précédente (7.2 → 7.0).

Le catalogue [`mapping/zabbix_templates_catalog.json`](mapping/zabbix_templates_catalog.json) reprend les
templates officiels publiés sur https://www.zabbix.com/integrations, lus dans le dossier `templates/` du dépôt
officiel Zabbix pour chaque version (6.0, 7.0, 7.4 et 8.0 — branche de développement à la date de génération).
Pour le mettre à jour (nouvelle version de Zabbix) :

```bash
python3 tools/build_zabbix_catalog.py                      # versions par défaut
python3 tools/build_zabbix_catalog.py 7.0=release/7.0 8.0=release/8.0
```

## Installation

```bash
python3 -m pip install -r requirements.txt
```

Python ≥ 3.9 (3.10+ recommandé), Windows ou Linux. Dépendances : `requests`, `openpyxl`, `PyYAML`,
`Flask` (interface web), `truststore` (certificats du système) et `PyMySQL` (uniquement pour la
lecture directe en base).

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
| `--collecte-client` | Ne collecter que les hôtes du client (`--client-hotes`), leurs services et les templates utilisés : beaucoup plus rapide (case « Limiter la collecte au client » dans l'interface web) |
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

Les tests utilisent un faux serveur Centreon (`tests/fake_centreon.py`) et un faux Zabbix
(`tests/fake_zabbix.py`). Ceux de l'extraction Centreon reposent sur un faux serveur
qui reproduit le format des réponses de l'API v1 (CLAPI) et de l'API v2, établi d'après le code
source de Centreon. Faites un premier essai sur votre instance et comparez les volumes
(nombre d'hôtes / services) avec l'interface. De même, le client Zabbix suit la documentation de
l'API Zabbix 5.0 à 7.x mais n'a été testé que contre le faux serveur : vérifiez le nombre d'hôtes lus.
