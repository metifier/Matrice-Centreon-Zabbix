"""Construction des tableaux de la matrice de décision à partir d'un Inventory."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .mapping import Mapper, Suggestion
from .model import NO_TEMPLATE, Host, Inventory, Service, _uniq, parse_check_line

# Décisions proposées (et valeurs de la liste déroulante « Décision finale »).
MIGRER_NATIF = "Migrer (template natif)"
MIGRER_ADAPTER = "Migrer (adapter)"
MIGRER_DEV = "Migrer (développer)"
NE_PAS_MIGRER = "Ne pas migrer"
A_ANALYSER = "À analyser"
HERITE = "Hérité (pas d'action directe)"
DECISIONS = [MIGRER_NATIF, MIGRER_ADAPTER, MIGRER_DEV, NE_PAS_MIGRER, A_ANALYSER, HERITE]
HOST_DECISIONS = ["Migrer", NE_PAS_MIGRER, A_ANALYSER]
SUMMARY_HEADERS = {"Proposition par template de service", "Services par catégorie", "Services par stratégie",
                   "Hôtes Centreon par statut Zabbix", "Services Centreon par statut Zabbix"}

_STRATEGY_TO_DECISION = {"Natif": MIGRER_NATIF, "Adapter": MIGRER_ADAPTER,
                         "Développer": A_ANALYSER, "Abandonner": NE_PAS_MIGRER}
_EFFORT = {"Natif": "Faible", "Adapter": "Moyen", "Développer": "Élevé", "Abandonner": "Nul"}

_SECRET_RE = re.compile(
    r"(--?(?:password|passwd|pass|community|snmp-community|authpassphrase|privpassphrase|"
    r"api-password|api-token|token|secret|apikey|api-key)[= ]+)('[^']*'|\"[^\"]*\"|\S+)",
    re.IGNORECASE)
_SNMP_COMMUNITY_RE = re.compile(r"(\s-C\s+)(\S+)")


def mask_secrets(line: str) -> str:
    line = _SECRET_RE.sub(lambda m: m.group(1) + "********", line)
    return _SNMP_COMMUNITY_RE.sub(lambda m: m.group(1) + "********", line)


def priority(nb_hosts: int, high: int, medium: int) -> str:
    if nb_hosts >= high:
        return "Haute"
    if nb_hosts >= medium:
        return "Moyenne"
    return "Basse" if nb_hosts else "-"


def service_key(svc: Service) -> str:
    """Clé de regroupement d'un service dans la matrice : son template direct."""
    if svc.template:
        return svc.template
    return f"{NO_TEMPLATE} {svc.check_command or 'sans commande'}"


@dataclass
class Matrix:
    inventory: Inventory
    service_templates: list[dict] = field(default_factory=list)
    host_templates: list[dict] = field(default_factory=list)
    hosts: list[dict] = field(default_factory=list)
    services: list[dict] = field(default_factory=list)
    commands: list[dict] = field(default_factory=list)
    host_x_htpl: tuple[list[str], list[dict]] = ((), [])
    host_x_stpl: tuple[list[str], list[dict]] = ((), [])
    summary: list[tuple] = field(default_factory=list)
    # Renseignés par compare.compare_with_zabbix()
    zabbix_hosts: list[dict] = field(default_factory=list)
    zabbix_only: list[dict] = field(default_factory=list)
    zabbix_templates: list[dict] = field(default_factory=list)
    zabbix_summary: list[tuple] = field(default_factory=list)
    client: object = None  # filters.ClientFilter du périmètre affiché
    correlation: list[dict] = field(default_factory=list)  # services Centreon <-> items Zabbix
    item_matches: dict = field(default_factory=dict)  # (template Zabbix, clé d'item) -> Counter(template de service)
    zabbix_elements: list[dict] = field(default_factory=list)  # sélection des items / problèmes Zabbix


def build_matrix(inv: Inventory, mapper: Mapper | None = None, high: int = 20, medium: int = 5,
                 mask: bool = True) -> Matrix:
    mapper = mapper or Mapper()
    m = Matrix(inventory=inv)
    clean = mask_secrets if mask else (lambda s: s)

    # ------------------------------------------------------------------ services
    by_key: dict[str, list[Service]] = defaultdict(list)
    svc_by_host: dict[str, list[dict]] = defaultdict(list)
    chain_usage: Counter = Counter()  # services utilisant un template, directement ou par héritage
    for svc in inv.services:
        host = inv.hosts.get(svc.host) or Host(name=svc.host)
        chk = inv.effective_check(svc, host)
        chain = inv.stpl_chain(svc.template)
        sug = mapper.for_service(chain or [svc.description], chk.command, chk.plugin, chk.mode, chk.line)
        key = service_key(svc)
        by_key[key].append(svc)
        for t in chain[1:]:
            chain_usage[t] += 1
        active = svc.activate and host.activate
        proposal = _STRATEGY_TO_DECISION.get(sug.strategy, A_ANALYSER) if active else NE_PAS_MIGRER
        row = {
            "Hôte": svc.host, "Adresse": host.address, "Collecteur": host.poller,
            "Service": svc.description, "Actif": "Oui" if active else "Non",
            "Hérité du groupe": svc.hostgroup, "Template de service": key,
            "Chaîne d'héritage": " > ".join(chain), "Commande": chk.command,
            "Plugin": chk.plugin, "Mode": chk.mode, "Arguments": clean(chk.args),
            "Ligne de commande résolue": clean(chk.line), "Catégorie": sug.category,
            "Template Zabbix suggéré": sug.zabbix_template, "Item Zabbix équivalent": sug.zabbix_item,
            "Stratégie": sug.strategy, "Proposition": proposal,
            "Décision (template)": None, "Décision (hôte)": None, "Décision (service)": "",
            "Décision effective": None,
            "Template Zabbix cible": "", "Template Zabbix retenu": "", "Templates Zabbix (hôte)": "",
            "Commentaire": "",
        }
        m.services.append(row)
        svc_by_host[svc.host].append(row)
    m.services.sort(key=lambda r: (r["Hôte"].lower(), r["Service"].lower()))

    # ---------------------------------------------------- matrice templates services
    all_keys = list(inv.service_templates) + [k for k in by_key if k not in inv.service_templates]
    for key in all_keys:
        svcs = by_key.get(key, [])
        tpl = inv.service_templates.get(key)
        if tpl:
            chk = inv.effective_check(tpl)
            chain = inv.stpl_chain(key)
            alias = tpl.alias
            linked_htpl = ", ".join(tpl.host_templates)
        else:  # services sans template : regroupés par commande
            first = svcs[0]
            chk = inv.effective_check(first, inv.hosts.get(first.host))
            chain, alias, linked_htpl = [], "", ""
        sug = mapper.for_service(chain or [svcs[0].description], chk.command, chk.plugin, chk.mode, chk.line)
        if not svcs and sug.rule == "défaut":
            sug = Suggestion(strategy="", confidence="")
        hosts = _uniq(s.host for s in svcs)
        active = [s for s in svcs if s.activate and inv.hosts.get(s.host, Host(s.host)).activate]
        indirect = chain_usage.get(key, 0)
        if not svcs and indirect:
            proposal, reason = HERITE, f"Parent de templates utilisés ({indirect} services)"
        elif not svcs:
            proposal, reason = NE_PAS_MIGRER, "Template inutilisé"
        elif not active:
            proposal, reason = NE_PAS_MIGRER, "Tous les services sont désactivés"
        else:
            proposal = _STRATEGY_TO_DECISION.get(sug.strategy, A_ANALYSER)
            reason = sug.note or _reason(sug)
        m.service_templates.append({
            "Template de service": key, "Alias": alias, "Chaîne d'héritage": " > ".join(chain),
            "Template pack (racine)": inv.pack_template(key) if tpl else "",
            "Templates d'hôte liés": linked_htpl, "Commande": chk.command, "Plugin": chk.plugin,
            "Mode": chk.mode, "Arguments": clean(chk.args),
            "Nb services": len(svcs), "Nb services actifs": len(active), "Nb hôtes": len(hosts),
            "Nb services (hérités)": indirect,
            "Exemples d'hôtes": ", ".join(hosts[:5]) + (" ..." if len(hosts) > 5 else ""),
            "Descriptions de service": ", ".join(_uniq(s.description for s in svcs)[:5]),
            "Catégorie": sug.category, "Template Zabbix suggéré": sug.zabbix_template,
            "Item Zabbix équivalent": sug.zabbix_item, "Stratégie": sug.strategy,
            "Confiance": sug.confidence, "Effort": _EFFORT.get(sug.strategy, "?") if svcs else "-",
            "Priorité": priority(len(hosts), high, medium), "Justification": reason,
            "Proposition": proposal, "Décision finale": "", "Template / item Zabbix cible": "",
            "Commentaire": "",
        })
    m.service_templates.sort(key=lambda r: (-r["Nb services"], -r["Nb services (hérités)"],
                                            r["Template de service"].lower()))

    # ------------------------------------------------------- matrice templates hôtes
    hosts_all_tpl = {h.name: inv.host_all_templates(h) for h in inv.hosts.values()}
    chains_by_host: dict[str, list[set]] = defaultdict(list)
    for s in inv.services:
        chains_by_host[s.host].append(set(inv.stpl_chain(s.template)))
    for name, tpl in inv.host_templates.items():
        direct = [h for h in inv.hosts.values() if name in h.templates]
        total = [h for h in inv.hosts.values() if name in hosts_all_tpl[h.name]]
        linked = set(tpl.service_templates)
        nb_svc = sum(1 for h in total for c in chains_by_host[h.name] if linked & c)
        chain = inv.htpl_ancestors([name])
        sug = mapper.for_host_template(chain)
        if not total:
            proposal, reason = NE_PAS_MIGRER, "Template inutilisé"
        elif not direct:
            proposal, reason = HERITE, f"Hérité par {len(total)} hôte(s) via d'autres templates"
        elif not any(h.activate for h in total):
            proposal, reason = NE_PAS_MIGRER, "Tous les hôtes sont désactivés"
        else:
            proposal = _STRATEGY_TO_DECISION.get(sug.strategy, A_ANALYSER)
            reason = sug.note or _reason(sug)
        m.host_templates.append({
            "Template d'hôte": name, "Alias": tpl.alias, "Parents": ", ".join(tpl.parents),
            "Chaîne complète": " > ".join(chain), "Templates de service liés": ", ".join(tpl.service_templates),
            "Nb hôtes (direct)": len(direct), "Nb hôtes (total)": len(total),
            "Nb services issus du template": nb_svc,
            "Exemples d'hôtes": ", ".join(h.name for h in total[:5]) + (" ..." if len(total) > 5 else ""),
            "Template Zabbix suggéré": sug.zabbix_template, "Stratégie": sug.strategy,
            "Confiance": sug.confidence, "Priorité": priority(len(total), high, medium),
            "Justification": reason, "Proposition": proposal, "Décision finale": "",
            "Template Zabbix cible": "", "Commentaire": "",
        })
    m.host_templates.sort(key=lambda r: (-r["Nb hôtes (direct)"], -r["Nb hôtes (total)"],
                                         r["Template d'hôte"].lower()))

    # --------------------------------------------------------------------- hôtes
    for h in sorted(inv.hosts.values(), key=lambda x: x.name.lower()):
        rows = svc_by_host.get(h.name, [])
        all_tpl = hosts_all_tpl[h.name]
        zbx = []
        for t in h.templates:
            s = mapper.for_host_template(inv.htpl_ancestors([t]))
            if s.zabbix_template:
                zbx.append(s.zabbix_template)
        zbx += [r["Template Zabbix suggéré"] for r in rows
                if r["Stratégie"] == "Natif" and r["Template Zabbix suggéré"]
                and not r["Template Zabbix suggéré"].startswith("(")]
        m.hosts.append({
            "Hôte": h.name, "Alias": h.alias, "Adresse": h.address, "Collecteur": h.poller,
            "Groupes d'hôtes": ", ".join(h.hostgroups), "Templates (directs)": ", ".join(h.templates),
            "Templates (tous)": ", ".join(all_tpl), "Actif": "Oui" if h.activate else "Non",
            "Nb services": len(rows), "Nb services actifs": sum(r["Actif"] == "Oui" for r in rows),
            "Nb services spécifiques": sum(r["Stratégie"] == "Développer" for r in rows),
            "Interface Zabbix suggérée": _interface(all_tpl, rows),
            "Templates Zabbix suggérés": ", ".join(_uniq(zbx)),
            "Proxy Zabbix suggéré": h.poller,
            "Proposition": "Migrer" if h.activate else NE_PAS_MIGRER,
            "Décision finale": "", "Templates Zabbix cibles": "", "Templates Zabbix retenus": "",
            "Templates Zabbix exclus": "",
            "Commentaire": "",
        })

    # ----------------------------------------------------------------- commandes
    cmd_usage = Counter(r["Commande"] for r in m.services)
    for c in sorted(inv.commands.values(), key=lambda x: x.name.lower()):
        parsed = parse_check_line(c.line, c.name)
        m.commands.append({"Commande": c.name, "Type": c.type, "Exécutable": parsed["executable"],
                           "Plugin": parsed["plugin"], "Mode": parsed["mode"],
                           "Nb services": cmd_usage.get(c.name, 0), "Ligne de commande": clean(c.line)})

    # --------------------------------------------------------- matrices croisées
    m.host_x_htpl = _cross_htpl(inv, hosts_all_tpl)
    m.host_x_stpl = _cross_stpl(m.services)
    m.summary = _summary(inv, m)
    return m


def _reason(sug: Suggestion) -> str:
    if sug.rule == "défaut":
        return "Aucune correspondance connue : check spécifique"
    if sug.zabbix_template:
        return f"Équivalent Zabbix : {sug.zabbix_template}"
    return f"Équivalent Zabbix : {sug.zabbix_item}" if sug.zabbix_item else ""


def _interface(templates: list[str], rows: list[dict]) -> str:
    text = " ".join(templates + [r["Plugin"] + " " + r["Template de service"] + " " + r["Commande"]
                                 for r in rows]).lower()
    kinds = []
    if re.search(r"(nrpe|nsclient|ssh|local|agent|wsman|wmi|mysql|postgres|mssql|oracle)", text):
        kinds.append("Agent")
    if "snmp" in text:
        kinds.append("SNMP")
    if "jmx" in text or "tomcat" in text:
        kinds.append("JMX")
    if "ipmi" in text:
        kinds.append("IPMI")
    return " + ".join(kinds) or "Agent (à confirmer)"


def _cross_htpl(inv: Inventory, hosts_all_tpl: dict) -> tuple[list[str], list[dict]]:
    usage = Counter(t for tpls in hosts_all_tpl.values() for t in tpls)
    columns = [t for t, _ in usage.most_common()]
    rows = []
    for h in sorted(inv.hosts.values(), key=lambda x: x.name.lower()):
        row = {"Hôte": h.name}
        for t in columns:
            row[t] = "D" if t in h.templates else ("h" if t in hosts_all_tpl[h.name] else "")
        rows.append(row)
    return columns, rows


def _cross_stpl(services: list[dict]) -> tuple[list[str], list[dict]]:
    usage = Counter(r["Template de service"] for r in services)
    columns = [t for t, _ in usage.most_common()]
    per_host: dict[str, Counter] = defaultdict(Counter)
    for r in services:
        per_host[r["Hôte"]][r["Template de service"]] += 1
    rows = [{"Hôte": h, **{t: (c[t] or "") for t in columns}}
            for h, c in sorted(per_host.items(), key=lambda x: x[0].lower())]
    return columns, rows


def _summary(inv: Inventory, m: Matrix) -> list[tuple]:
    used_stpl = sum(1 for r in m.service_templates if r["Nb services"] or r["Nb services (hérités)"])
    used_htpl = sum(1 for r in m.host_templates if r["Nb hôtes (total)"])
    out = [
        ("Source", inv.source),
        ("Hôtes", len(inv.hosts)),
        ("Hôtes actifs", sum(h.activate for h in inv.hosts.values())),
        ("Collecteurs (pollers)", len({h.poller for h in inv.hosts.values() if h.poller})),
        ("Services", len(m.services)),
        ("Services actifs", sum(r["Actif"] == "Oui" for r in m.services)),
        ("Templates d'hôte (utilisés / total)", f"{used_htpl} / {len(inv.host_templates)}"),
        ("Templates de service (utilisés / total)",
         f"{used_stpl} / {sum(1 for r in m.service_templates if r['Template de service'] in inv.service_templates)}"),
        ("Commandes", len(inv.commands)),
        (None, None),
        ("Proposition par template de service", "Nb templates | Nb services"),
    ]
    stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in m.service_templates:
        stats[r["Proposition"]][0] += 1
        stats[r["Proposition"]][1] += r["Nb services"]
    for d in DECISIONS:
        if d in stats:
            out.append((d, f"{stats[d][0]} | {stats[d][1]}"))
    out += [(None, None), ("Services par catégorie", "Nb services")]
    for cat, n in Counter(r["Catégorie"] or "?" for r in m.services).most_common():
        out.append((cat, n))
    out += [(None, None), ("Services par stratégie", "Nb services")]
    for st, n in Counter(r["Stratégie"] for r in m.services).most_common():
        out.append((st, n))
    return out
