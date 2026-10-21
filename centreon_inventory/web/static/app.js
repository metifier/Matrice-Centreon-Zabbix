"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const BADGES = {
  "Migrer (template natif)": "b-ok", "Migrer (adapter)": "b-info", "Migrer (développer)": "b-dev", "Migrer": "b-ok",
  "Ne pas migrer": "", "À analyser": "b-warn", "Hérité (pas d'action directe)": "",
  "Présent – templates OK": "b-ok", "Couvert dans Zabbix": "b-ok", "Présent": "b-info",
  "Présent – templates à lier": "b-warn", "Template à lier": "b-warn", "À créer dans Zabbix": "b-dev",
  "Absent de Zabbix": "b-bad", "Hôte absent de Zabbix": "b-bad", "Non migré (décision)": "",
  "Non (à importer)": "b-bad", "Natif": "b-ok", "Adapter": "b-info", "Développer": "b-dev", "Abandonner": "",
  "Haute": "b-bad", "Moyenne": "b-warn", "Basse": "",
  "Couvert par un item standard": "b-ok", "Template standard sans item correspondant": "b-warn",
  "Template standard à importer dans Zabbix": "b-info", "Spécificité à créer": "b-dev", "Non migré": "",
  "Surveiller": "b-ok", "Facultatif": "b-info", "Désactiver": "",
  "Désastre": "b-bad", "Avertissement": "b-warn", "Information": "", "Non classée": "",
};
const BADGE_COLUMNS = new Set(["Couverture standard", "Proposition", "Décision effective", "Décision (template)", "Décision (hôte)", "Statut Zabbix",
  "Disponible dans Zabbix", "Stratégie", "Priorité", "Sévérité"]);
const NUMERIC = (v) => typeof v === "number";
// Champs « template Zabbix cible » : saisie libre assistée par l'auto-complétion du catalogue Zabbix ; plusieurs
// templates possibles, séparés par des virgules.
const TEMPLATE_FIELDS = new Set(["Template Zabbix cible", "Template / item Zabbix cible", "Templates Zabbix cibles"]);
const splitList = (v) => String(v || "").split(",").map((x) => x.trim()).filter(Boolean);

const app = {
  view: "dashboard",
  state: null,
  tables: {},
  scope: { client: "", hosts: "", hostgroups: "", pollers: "", services: "", exclude: "", mode: "et" },
  lookup: null,
};
const SCOPE_FIELDS = ["hosts", "hostgroups", "pollers", "services", "exclude"];
const EMPTY_SCOPE = { client: "", hosts: "", hostgroups: "", pollers: "", services: "", exclude: "", mode: "et" };
const scopeFromClient = (c) => ({ client: c.name, hosts: c.hosts || "", hostgroups: c.hostgroups || "",
  pollers: c.pollers || "", services: c.services || "", exclude: c.exclude || "", mode: c.mode || "et" });
const scopeActive = (s) => SCOPE_FIELDS.some((k) => s[k]);

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), 3500);
}

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.error) || data || `HTTP ${r.status}`);
  return data;
}
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function scopeQuery() {
  const s = app.scope, p = new URLSearchParams();
  if (s.client) p.set("client", s.client);
  else {
    for (const k of SCOPE_FIELDS) if (s[k]) p.set(k, s[k]);
    if (scopeActive(s)) p.set("mode", s.mode);
  }
  return p;
}

function scopeLabel() {
  const s = app.scope;
  if (s.client) {
    const c = (app.state.clients || []).find((x) => x.name === s.client) || {};
    return `Client « ${s.client} » — ${describe(c)}`;
  }
  if (scopeActive(s)) return `Filtre — ${describe(s)} (${s.mode})`;
  return "";
}

function describe(c) {
  const parts = [`hôtes : ${c.hosts || "*"}`];
  if (c.hostgroups) parts.push(`groupes : ${c.hostgroups}`);
  if (c.pollers) parts.push(`collecteurs : ${c.pollers}`);
  parts.push(`services : ${c.services || "*"}`, `exclusions : ${c.exclude || "aucune"}`);
  return parts.join(" · ");
}

function updateExportLink() {
  $("#export").href = "/api/export.xlsx?" + scopeQuery().toString();
}

// ------------------------------------------------------------------ état global
async function loadState() {
  app.state = await api("/api/state");
  const st = app.state;
  const sel = $("#scope-client");
  const current = app.scope.client;
  sel.innerHTML = '<option value="">Tous les hôtes</option>' +
    st.clients.map((c) => `<option ${c.name === current ? "selected" : ""}>${esc(c.name)}</option>`).join("");
  const s = st.settings || {};
  const fc = $("#form-centreon"), fz = $("#form-zabbix");
  if (s.centreon_url && !fc.url.value) fc.url.value = s.centreon_url;
  if (s.centreon_user && !fc.user.value) fc.user.value = s.centreon_user;
  fc.insecure.checked = !!s.centreon_insecure;
  if (s.zabbix_url && !fz.url.value) fz.url.value = s.zabbix_url;
  if (s.zabbix_user && !fz.user.value) fz.user.value = s.zabbix_user;
  fz.insecure.checked = !!s.zabbix_insecure;
  $("#status-centreon").innerHTML = st.centreon
    ? `Inventaire du ${esc(st.centreon.date)} — ${st.centreon.hosts} hôtes, ${st.centreon.services} services<br><span class="muted">${esc(st.centreon.source)}</span>`
    : '<span class="muted">Aucun inventaire Centreon.</span>';
  $("#status-zabbix").innerHTML = st.zabbix
    ? `Inventaire du ${esc(st.zabbix.date)} — Zabbix ${esc(st.zabbix.version)}, ${st.zabbix.hosts} hôtes, ${st.zabbix.templates} templates`
    : '<span class="muted">Aucun inventaire Zabbix.</span>';
  $$(".tabs .zbx").forEach((b) => b.classList.toggle("disabled", !st.zabbix));
  const tv = $("#target-version"), versions = (st.catalog && st.catalog.versions) || [];
  tv.innerHTML = `<option value="">Auto${st.zabbix ? ` (Zabbix inventorié : ${esc(st.zabbix.version)})` : " (Zabbix inventorié, sinon la plus récente)"}</option>` +
    versions.map((v) => `<option value="${esc(v)}" ${v === s.zabbix_target_version ? "selected" : ""}>Zabbix ${esc(v)}</option>`).join("");
  renderClients();
}

// ------------------------------------------------------------------ navigation
function show(view) {
  app.view = view;
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.remove("active"));
  const id = ["dashboard", "sources", "clients"].includes(view) ? view : "table";
  $(`#view-${id}`).classList.add("active");
  try { localStorage.setItem("view", view); } catch (e) { /* stockage indisponible */ }
  refresh();
}

function refresh() {
  updateExportLink();
  if (app.view === "dashboard") return loadDashboard();
  if (["sources", "clients"].includes(app.view)) return;
  return loadTable();
}

// ------------------------------------------------------------------ tableau de bord
function bars(el, entries, total) {
  const max = Math.max(1, ...entries.map((e) => e[1]));
  el.innerHTML = entries.length ? `<div class="bars">${entries.map(([label, n, extra]) => `
    <div class="bar"><span title="${esc(label)}"><span class="badge ${BADGES[label] || ""}">${esc(label || "—")}</span></span>
    <div class="track"><div class="fill" style="width:${(100 * n) / max}%"></div></div>
    <span class="n">${n}${extra ? ` <span class="small">${extra}</span>` : ""}</span></div>`).join("")}</div>`
    : '<p class="muted">Aucune donnée.</p>';
}

async function loadDashboard() {
  const d = await api("/api/summary?" + scopeQuery());
  $("#empty").classList.toggle("hidden", !d.empty);
  const label = scopeLabel();
  $("#scope-banner").classList.toggle("hidden", !label);
  $("#scope-banner").textContent = label;
  if (d.empty) { $("#kpis").innerHTML = ""; return; }
  const k = d.kpi;
  const tiles = [
    ["Hôtes", k.hosts], ["Services", k.services], ["Services actifs", k.services_actifs],
    ["Templates de service utilisés", k.service_templates], ["Templates d'hôte utilisés", k.host_templates],
  ];
  if (k.zabbix) tiles.push(["Hôtes déjà dans Zabbix", `${k.zabbix_present} / ${k.hosts}`], ["Hôtes uniquement dans Zabbix", k.zabbix_only]);
  $("#kpis").innerHTML = tiles.map(([l, v]) => `<div class="kpi"><div class="v">${esc(v)}</div><div class="l">${esc(l)}</div></div>`).join("");
  bars($("#chart-props"), Object.entries(d.propositions).map(([k2, [n, s]]) => [k2, n, `tpl · ${s} svc`]).sort((a, b) => b[1] - a[1]));
  bars($("#chart-decisions"), Object.entries(d.decisions_effectives).sort((a, b) => b[1] - a[1]));
  const zbx = Object.entries(d.statuts_zabbix).sort((a, b) => b[1] - a[1]);
  const cov = Object.entries(d.couverture || {}).sort((a, b) => b[1] - a[1]);
  if (cov.length) bars($("#chart-cov"), cov);
  else $("#chart-cov").innerHTML = '<p class="muted">Lancez l\'inventaire Zabbix pour voir quels services sont déjà couverts par les items des templates standards.</p>';
  const sel = Object.entries(d.selection || {}).map(([dec, kinds]) => [dec, Object.values(kinds).reduce((a, b) => a + b, 0),
    Object.entries(kinds).map(([k2, n]) => `${n} ${k2.toLowerCase()}`).join(" · ")]);
  const selOrder = ["Surveiller", "Facultatif", "Désactiver"];
  sel.sort((a, b) => selOrder.indexOf(a[0]) - selOrder.indexOf(b[0]));
  if (sel.length) bars($("#chart-sel"), sel);
  else $("#chart-sel").innerHTML = '<p class="muted">Lancez l\'inventaire Zabbix pour choisir les items et problèmes (triggers) des templates retenus à surveiller.</p>';
  if (zbx.length) bars($("#chart-zbx"), zbx);
  else $("#chart-zbx").innerHTML = '<p class="muted">Lancez l\'inventaire Zabbix (onglet Sources) pour comparer.</p>';
  $("#top").innerHTML = `<table class="grid"><thead><tr><th>Template</th><th>Services</th><th>Hôtes</th><th>Proposition</th><th>Zabbix</th></tr></thead><tbody>${
    d.top.map((r) => `<tr><td title="${esc(r["Template de service"])}">${esc(r["Template de service"])}</td><td class="num">${r["Nb services"]}</td><td class="num">${r["Nb hôtes"]}</td><td><span class="badge ${BADGES[r.Proposition] || ""}">${esc(r.Proposition)}</span></td><td>${esc(r["Template Zabbix suggéré"])}</td></tr>`).join("")}</tbody></table>`;
  const kv = (rows) => rows.map(([a, b]) => a === "" && b === "" ? '<tr><td colspan="2">&nbsp;</td></tr>'
    : `<tr class="${typeof b === "string" && b.startsWith("Nb") ? "h" : ""}"><td>${esc(a)}</td><td>${esc(b)}</td></tr>`).join("");
  $("#summary").innerHTML = kv(d.summary);
  $("#zsummary").innerHTML = kv(d.zabbix_summary);
}

// ------------------------------------------------------------------ tables
function tstate() {
  return (app.tables[app.view] ||= { page: 1, size: 100, sort: "", dir: "asc", q: "", filters: {}, sel: new Set() });
}

function tableQuery(ts) {
  const p = scopeQuery();
  if (ts.q) p.set("q", ts.q);
  for (const [c, v] of Object.entries(ts.filters)) if (v) p.set("f." + c, v);
  return p;
}

let tableTimer;
function debounced(fn) { clearTimeout(tableTimer); tableTimer = setTimeout(fn, 300); }

async function loadTable() {
  const view = app.view, ts = tstate();
  const p = scopeQuery();
  p.set("page", ts.page); p.set("size", ts.size);
  if (ts.sort) { p.set("sort", ts.sort); p.set("dir", ts.dir); }
  if (ts.q) p.set("q", ts.q);
  for (const [c, v] of Object.entries(ts.filters)) if (v) p.set("f." + c, v);
  const d = await api(`/api/table/${view}?` + p);
  if (view !== app.view) return;
  app.lastTable = d;
  $("#search").value = ts.q;
  $("#size").value = String(ts.size);
  renderTable(d, ts);
}

function renderTable(d, ts) {
  const grid = $("#grid"), thead = $("thead", grid), tbody = $("tbody", grid);
  const editable = new Set(d.editable || []);
  if (!d.columns.length) {
    thead.innerHTML = "";
    tbody.innerHTML = `<tr><td class="muted">${app.view.startsWith("zabbix") ? "Aucune donnée : lancez l'inventaire Zabbix (onglet Sources)." : "Aucune donnée pour ce périmètre."}</td></tr>`;
    $("#count").textContent = ""; $("#pager").innerHTML = "";
    return;
  }
  const selectable = editable.size > 0, sel = ts.sel;
  const pageKeys = d.rows.map((r) => r._key);
  const allOnPage = selectable && pageKeys.length && pageKeys.every((k) => sel.has(k));
  const selHead = selectable ? `<th class="sel"><input type="checkbox" data-sel-page title="Sélectionner la page" ${allOnPage ? "checked" : ""}></th>` : "";
  const selFilter = selectable ? '<th class="sel"></th>' : "";
  thead.innerHTML = `<tr>${selHead}${d.columns.map((c) => `<th data-col="${esc(c)}" title="Trier">${esc(c)}${ts.sort === c ? `<span class="arrow">${ts.dir === "asc" ? "▲" : "▼"}</span>` : ""}</th>`).join("")}</tr>
    <tr class="filters">${selFilter}${d.columns.map((c) => {
      const v = ts.filters[c] || "";
      if (d.values && d.values[c]) {
        return `<th><select data-filter="${esc(c)}"><option value="">(tous)</option>${d.values[c].map((x) => `<option value="=${esc(x)}" ${v === "=" + x ? "selected" : ""}>${esc(x)}</option>`).join("")}</select></th>`;
      }
      return `<th><input data-filter="${esc(c)}" value="${esc(v)}" placeholder="filtrer"></th>`;
    }).join("")}</tr>`;
  tbody.innerHTML = d.rows.map((r) => {
    const checked = selectable && sel.has(r._key);
    const box = selectable ? `<td class="sel"><input type="checkbox" data-sel="${esc(r._key)}" ${checked ? "checked" : ""}></td>` : "";
    return `<tr class="${checked ? "selected" : ""}">${box}${d.columns.map((c, i) => cell(c, r, editable, d.choices, i === 0)).join("")}</tr>`;
  }).join("");
  freezeColumns(grid, (selectable ? 1 : 0) + FROZEN_COLUMNS);
  updateBulk();
  $("#count").textContent = `${d.total} ligne(s)` + (d.total !== d.all ? ` sur ${d.all}` : "");
  const pages = Math.max(1, Math.ceil(d.total / d.size));
  $("#pager").innerHTML = pages > 1 ? `<button data-page="1" ${d.page === 1 ? "disabled" : ""}>«</button>
    <button data-page="${d.page - 1}" ${d.page === 1 ? "disabled" : ""}>‹</button>
    <span>Page ${d.page} / ${pages}</span>
    <button data-page="${d.page + 1}" ${d.page >= pages ? "disabled" : ""}>›</button>
    <button data-page="${pages}" ${d.page >= pages ? "disabled" : ""}>»</button>` : "";
}

// Fige les premières colonnes (case de sélection comprise) lors du défilement horizontal : chaque cellule reçoit
// un décalage « left » égal à la largeur cumulée des colonnes qui la précèdent.
const FROZEN_COLUMNS = 3;

function freezeColumns(grid, n) {
  const head = grid.querySelector("thead tr");
  if (!head) return;
  const cells = [...head.children].slice(0, n), lefts = cells.map((th) => th.offsetLeft - cells[0].offsetLeft);
  for (const tr of grid.rows) {
    [...tr.children].forEach((c, i) => {
      const frozen = i < n && tr.children.length > 1;
      c.classList.toggle("frz", frozen);
      c.classList.toggle("frz-last", frozen && i === n - 1);
      c.style.left = frozen ? lefts[i] + "px" : "";
    });
  }
}

function updateBulk() {
  const d = app.lastTable, ts = tstate(), bar = $("#bulk");
  const fields = (d && d.editable) || [];
  if (!fields.length || !ts.sel.size || ["dashboard", "sources", "clients"].includes(app.view)) {
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");
  $("#bulk-count").textContent = `${ts.sel.size} ligne(s) sélectionnée(s)`;
  $("#bulk-all").textContent = `Sélectionner les ${d.total} résultats filtrés`;
  $("#bulk-all").classList.toggle("hidden", ts.sel.size >= d.total);
  const fsel = $("#bulk-field"), current = fsel.value;
  fsel.innerHTML = fields.map((f) => `<option ${f === current ? "selected" : ""}>${esc(f)}</option>`).join("");
  renderBulkValue();
}

function renderBulkValue() {
  const d = app.lastTable, field = $("#bulk-field").value, opts = d && d.choices && d.choices[field];
  $("#bulk-value-wrap").innerHTML = opts
    ? `<select id="bulk-value"><option value="">(effacer)</option>${opts.map((o) => `<option>${esc(o)}</option>`).join("")}</select>`
    : `<input id="bulk-value" placeholder="(vide = effacer)"${TEMPLATE_FIELDS.has(field) ? " data-ac autocomplete=\"off\"" : ""}>`;
}

async function applyBulk() {
  const ts = tstate(), field = $("#bulk-field").value, value = $("#bulk-value").value;
  const n = ts.sel.size;
  if (!confirm(`Appliquer « ${field} = ${value || "(vide)"} » à ${n} ligne(s) ?`)) return;
  try {
    await post("/api/decisions/bulk", { table: app.view, keys: [...ts.sel], field, value });
    ts.sel.clear();
    toast(`${n} ligne(s) mises à jour`);
    loadTable();
  } catch (e) { toast("Erreur : " + e.message); }
}

// Templates retenus d'un hôte : « × » désélectionne un template du cumul ; les exclus sont barrés, « ↺ » les rétablit.
function templateChips(c, r) {
  const key = esc(r._key), names = splitList(r[c]);
  if (c === "Templates Zabbix retenus") {
    return `<td class="tchips">${names.map((t) => `<span class="tchip">${esc(t)}<button data-untpl="${esc(t)}" data-key="${key}" title="Désélectionner ce template">×</button></span>`).join("")}</td>`;
  }
  return `<td class="tchips">${names.map((t) => `<span class="tchip off">${esc(t)}<button data-retpl="${esc(t)}" data-key="${key}" title="Rétablir ce template">↺</button></span>`).join("")}</td>`;
}

async function toggleTemplate(btn) {
  const row = ((app.lastTable && app.lastTable.rows) || []).find((r) => r._key === btn.dataset.key);
  if (!row) return;
  const lower = (t) => t.toLowerCase(), save = (field, list) =>
    post("/api/decision", { table: "hosts", key: row._key, field, value: list.join(", ") });
  const cibles = splitList(row["Templates Zabbix cibles"]), exclus = splitList(row["Templates Zabbix exclus"]);
  try {
    if (btn.dataset.untpl !== undefined) {
      const t = btn.dataset.untpl;
      // Saisi sur l'hôte : on le retire des cibles ; venu du cumul (template d'hôte, service) : on l'exclut.
      if (cibles.some((x) => lower(x) === lower(t))) await save("Templates Zabbix cibles", cibles.filter((x) => lower(x) !== lower(t)));
      else await save("Templates Zabbix exclus", [...exclus, t]);
      toast(`« ${t} » désélectionné`);
    } else {
      const t = btn.dataset.retpl;
      await save("Templates Zabbix exclus", exclus.filter((x) => lower(x) !== lower(t)));
      toast(`« ${t} » rétabli`);
    }
    loadTable();
  } catch (e) { toast("Erreur : " + e.message); }
}

function cell(c, r, editable, choices, first) {
  const v = r[c];
  if (app.view === "hosts" && (c === "Templates Zabbix retenus" || c === "Templates Zabbix exclus")) return templateChips(c, r);
  if (first && r._key !== undefined) {
    return `<td title="Voir le détail"><a href="#" class="detail" data-detail="${esc(r._key)}">${esc(v)}</a></td>`;
  }
  if (editable.has(c)) {
    const opts = choices && choices[c];
    if (opts) {
      return `<td class="edit"><select data-edit="${esc(c)}" data-key="${esc(r._key)}"><option value=""></option>${opts.map((o) => `<option ${o === v ? "selected" : ""}>${esc(o)}</option>`).join("")}</select></td>`;
    }
    const ac = TEMPLATE_FIELDS.has(c) ? ` data-ac autocomplete="off" placeholder="taper ou choisir…" title="${esc(v || "Plusieurs templates : séparer par des virgules")}"` : "";
    return `<td class="edit"><input data-edit="${esc(c)}" data-key="${esc(r._key)}" value="${esc(v)}"${ac}></td>`;
  }
  if (BADGE_COLUMNS.has(c) && v !== "") return `<td><span class="badge ${BADGES[v] || ""}">${esc(v)}</span></td>`;
  return `<td class="${NUMERIC(v) ? "num" : ""}" title="${esc(v)}">${esc(v)}</td>`;
}

async function saveDecision(el) {
  try {
    await post("/api/decision", { table: app.view, key: el.dataset.key, field: el.dataset.edit, value: el.value });
    toast("Décision enregistrée");
    loadTable();
  } catch (e) { toast("Erreur : " + e.message); }
}

// ------------------------------------------------------------------ auto-complétion des templates Zabbix
// Propose les templates officiels de la version de Zabbix server visée (catalogue zabbix.com/integrations) et ceux
// du Zabbix inventorié, classés d'après le nom du template Centreon de la ligne.
const ac = { input: null, items: [], active: -1, seq: 0, timer: null };

function acContext(input) {
  const rows = (app.lastTable && app.lastTable.rows) || [];
  const row = input.dataset.key !== undefined ? rows.find((r) => r._key === input.dataset.key) : null;
  if (!row) return { context: "", suggested: "" };
  // Hôte : contexte = ses templates Centreon ; service : son template de service.
  const context = [row["Template de service"] || "", row["Templates (directs)"] || "", String(row._key)].filter(Boolean).join(" ");
  return { context, suggested: row["Template Zabbix suggéré"] || row["Templates Zabbix suggérés"] || "" };
}

// Segment en cours de saisie (après la dernière virgule) et templates déjà choisis dans le champ.
const acTyped = (input) => input.value.slice(input.value.lastIndexOf(",") + 1).trim();
const acChosen = (input) => splitList(input._typed ? input.value.slice(0, input.value.lastIndexOf(",") + 1) : input.value);

async function acOpen(input) {
  const seq = ++ac.seq, p = new URLSearchParams(acContext(input));
  if (input._typed && acTyped(input)) p.set("q", acTyped(input));
  let d;
  try { d = await api("/api/zabbix/templates?" + p); } catch (e) { return; }
  if (seq !== ac.seq || document.activeElement !== input) return;
  const chosen = acChosen(input), lower = new Set(chosen.map((x) => x.toLowerCase()));
  ac.input = input; ac.items = d.items.filter((t) => !lower.has(t.name.toLowerCase())); ac.active = -1;
  const list = $("#ac");
  const head = `Templates Zabbix ${esc(d.catalog_version || "?")}${d.version && d.catalog_version !== d.version.slice(0, d.catalog_version.length) ? ` (serveur ${esc(d.version)})` : ""}`
    + (p.get("q") ? "" : (p.get("context") ? ` — suggestions pour « ${esc(p.get("context"))} »` : " — tapez pour chercher"));
  const multi = chosen.length ? `<li class="head">Déjà choisi(s) : ${esc(chosen.join(", "))} — un clic ajoute un template</li>` : "";
  list.innerHTML = `<li class="head">${head}</li>${multi}` + (ac.items.length ? ac.items.map((t, i) => `<li data-i="${i}" role="option">
    <span class="n">${esc(t.name)}${t.present ? '<span class="badge b-ok">dans Zabbix</span>' : ""}${t.official ? "" : '<span class="badge">personnalisé</span>'}</span>
    <span class="d">${esc([t.group, t.description].filter(Boolean).join(" · "))}</span></li>`).join("")
    : '<li class="head">Aucun template correspondant (saisie libre possible).</li>');
  const r = input.getBoundingClientRect();
  list.classList.remove("hidden");
  list.style.left = Math.max(4, Math.min(r.left, window.innerWidth - list.offsetWidth - 4)) + "px";
  const below = window.innerHeight - r.bottom;
  list.style.top = (below < list.offsetHeight + 8 && r.top > below ? r.top - list.offsetHeight - 2 : r.bottom + 2) + "px";
}

function acClose() {
  ac.seq++; ac.input = null; ac.items = [];
  $("#ac").classList.add("hidden");
}

function acMove(delta) {
  const lis = $$("#ac li[data-i]");
  if (!lis.length) return;
  ac.active = (ac.active + delta + lis.length) % lis.length;
  lis.forEach((li, i) => li.classList.toggle("active", i === ac.active));
  lis[ac.active].scrollIntoView({ block: "nearest" });
}

function acPick(i) {
  const input = ac.input, t = ac.items[i];
  if (!input || !t) return;
  // Ajoute le template choisi à ceux déjà saisis (remplace seulement le segment en cours de frappe).
  input.value = [...acChosen(input), t.name].join(", ");
  acClose();
  if (input.dataset.edit !== undefined) input.dispatchEvent(new Event("change", { bubbles: true }));
}

function bindAutocomplete() {
  document.addEventListener("focusin", (e) => {
    if (e.target.matches("input[data-ac]")) { e.target._typed = false; acOpen(e.target); }
  });
  document.addEventListener("focusout", (e) => {
    if (e.target === ac.input) setTimeout(() => { if (document.activeElement !== ac.input) acClose(); }, 150);
  });
  document.addEventListener("input", (e) => {
    if (!e.target.matches("input[data-ac]")) return;
    e.target._typed = true;
    clearTimeout(ac.timer);
    ac.timer = setTimeout(() => acOpen(e.target), 150);
  });
  document.addEventListener("keydown", (e) => {
    if (!ac.input || e.target !== ac.input) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); acMove(e.key === "ArrowDown" ? 1 : -1); }
    else if (e.key === "Enter" && ac.active >= 0) { e.preventDefault(); acPick(ac.active); }
    else if (e.key === "Escape") { e.stopPropagation(); acClose(); }
  }, true);
  $("#ac").addEventListener("mousedown", (e) => {
    e.preventDefault(); // garde le focus dans le champ
    const li = e.target.closest("li[data-i]");
    if (li) acPick(+li.dataset.i);
  });
  window.addEventListener("scroll", (e) => { if (ac.input && !$("#ac").contains(e.target)) acClose(); }, true);
  $("#target-version").addEventListener("change", async (e) => {
    await post("/api/settings", { zabbix_target_version: e.target.value });
    toast(`Auto-complétion : templates Zabbix ${e.target.value || "de la version inventoriée"}`);
  });
}

// ------------------------------------------------------------------ fiche détaillée
const kvTable = (pairs) => `<table class="kv"><tbody>${pairs.map((p) =>
  `<tr><td>${esc(p.label)}</td><td>${BADGE_COLUMNS.has(p.label) ? `<span class="badge ${BADGES[p.value] || ""}">${esc(p.value)}</span>` : esc(p.value)}</td></tr>`).join("")}</tbody></table>`;
const macroTable = (macros) => Object.keys(macros || {}).length
  ? `<table class="grid"><thead><tr><th>Macro</th><th>Valeur</th></tr></thead><tbody>${Object.entries(macros).map(([k, v]) =>
    `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}</tbody></table>` : '<p class="muted small">Aucune macro.</p>';

function rowsTable(t) {
  if (!t || !t.rows.length) return '<p class="muted small">Aucun.</p>';
  return `<table class="grid"><thead><tr>${t.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${
    t.rows.map((r) => `<tr>${t.columns.map((c) => BADGE_COLUMNS.has(c) && r[c]
      ? `<td><span class="badge ${BADGES[r[c]] || ""}">${esc(r[c])}</span></td>`
      : `<td class="${c.startsWith("Ligne") ? "small" : ""}">${esc(r[c])}</td>`).join("")}</tr>`).join("")}</tbody></table>`
    + (t.total > t.rows.length ? `<p class="muted small">${t.rows.length} affichés sur ${t.total}.</p>` : "");
}

function zabbixSection(z) {
  if (z === null || z === undefined) return '<p class="muted small">Lancez l\'inventaire Zabbix pour voir les items des templates standards.</p>';
  if (!z.length) return '<p class="muted small">Aucun template Zabbix suggéré.</p>';
  return z.map((t) => `<h4>${esc(t.template)} ${t.present ? "" : '<span class="badge b-bad">absent de Zabbix</span>'}</h4>` +
    (t.present ? (t.items.length ? `<table class="grid"><thead><tr><th>Item / prototype</th><th>Clé</th><th>Type</th><th>Template</th></tr></thead><tbody>${
      t.items.map((i) => `<tr class="${i.match ? "match" : ""}"><td>${i.match ? "✔ " : ""}${esc(i.name)}</td><td class="small">${esc(i.key)}</td><td>${esc(i.type)}</td><td class="small">${esc(i.template)}</td></tr>`).join("")
    }</tbody></table>` + (t.total > t.items.length ? `<p class="muted small">${t.items.length} sur ${t.total}.</p>` : "")
      : '<p class="muted small">Aucun item inventorié (relancez l\'inventaire Zabbix).</p>') : "")).join("")
    + '<p class="muted small">✔ = item correspondant à la catégorie du service (règles « correlation » du référentiel).</p>';
}

function decideBox(view, key) {
  const d = app.lastTable;
  const field = { services: "Décision (service)", zabbix_elements: "Décision" }[view] || "Décision finale";
  const opts = d && d.choices && d.choices[field];
  if (!opts || !(d.editable || []).includes(field)) return "";
  const row = (d.rows || []).find((r) => r._key === key) || {};
  return `<div class="decide"><strong>${esc(field)}</strong>
    <select data-edit="${esc(field)}" data-key="${esc(key)}" id="drawer-decision"><option value=""></option>${
      opts.map((o) => `<option ${o === row[field] ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>
    <span class="muted small">enregistrée immédiatement</span></div>`;
}

async function openDetail(view, key) {
  let d;
  try { d = await api(`/api/detail/${view}?key=${encodeURIComponent(key)}&` + scopeQuery()); }
  catch (e) { return toast("Détail indisponible : " + e.message); }
  $("#drawer-kind").textContent = d.kind;
  $("#drawer-title").textContent = d.title;
  let html = decideBox(view, key);
  html += `<h3>Résumé</h3>${kvTable(d.summary || [])}`;
  if (d.example) html += `<h3>Ligne de commande (exemple : ${esc(d.example.service)})</h3><pre>${esc(d.example.line)}</pre>`;
  if (d.chain && d.chain.length) {
    html += `<h3>Chaîne d'héritage Centreon</h3>` + d.chain.map((c, i) => `<details ${i === 0 ? "open" : ""}><summary><strong>${esc(c.name)}</strong>${c.command ? ` — commande ${esc(c.command)}` : ""}</summary>
      ${c.args ? `<div class="small">Arguments : <code>${esc(c.args)}</code></div>` : ""}
      ${c.line ? `<pre>${esc(c.line)}</pre>` : ""}
      ${Object.keys(c.macros || {}).length ? macroTable(c.macros) : ""}
      ${c.host_templates && c.host_templates.length ? `<div class="small muted">Lié aux templates d'hôte : ${esc(c.host_templates.join(", "))}</div>` : ""}</details>`).join("");
  }
  if (d.no_template) html += '<p class="notice small">Services sans template de service : la commande est portée directement par chaque service (voir la liste ci-dessous). « sans commande » signifie que ni template ni commande n\'ont été lus : relancez la collecte et consultez le journal (diagnostic des templates).</p>';
  if (d.linked_service_templates) html += `<h3>Templates de service liés (${d.linked_service_templates.length})</h3>${rowsTable({
    columns: ["Template de service", "Nb services", "Catégorie", "Template Zabbix suggéré", "Proposition"],
    rows: d.linked_service_templates.map((x) => ({ "Template de service": x.name, ...(x.row || {}) })), total: d.linked_service_templates.length })}`;
  if (d.macros && Object.keys(d.macros).length) html += `<h3>Macros</h3>${macroTable(d.macros)}`;
  if ("zabbix" in d) html += `<h3>Items des templates Zabbix standards</h3>${zabbixSection(d.zabbix)}`;
  if (d.related) html += `<h3>${esc(d.related.title)} (${d.related.total})</h3>${rowsTable(d.related)}`;
  if (d.services) html += `<h3>Services${d.related ? " Centreon équivalents" : ""} (${d.services.total})</h3>${rowsTable(d.services)}`;
  if (d.hosts && d.hosts.length) html += `<h3>Hôtes (${d.hosts.length})</h3><div class="chips">${d.hosts.map((h) => `<span class="chip">${esc(h)}</span>`).join("")}</div>`;
  $("#drawer-body").innerHTML = html;
  $("#drawer").classList.remove("hidden");
  $("#drawer").scrollTop = 0;
}

// ------------------------------------------------------------------ extractions
async function followJob(id, statusEl) {
  const log = $("#log");
  for (;;) {
    const j = await api(`/api/jobs/${id}`);
    log.textContent = `[${j.kind}] ${j.status}\n` + j.logs.join("\n");
    log.scrollTop = log.scrollHeight;
    if (j.status !== "en cours") {
      toast(j.status === "terminé" ? `${j.kind} : terminé` : `${j.kind} : ${j.error}`);
      await loadState();
      if (statusEl && j.status === "erreur") statusEl.innerHTML += `<br><span class="badge b-bad">${esc(j.error)}</span>`;
      return;
    }
    await new Promise((res) => setTimeout(res, 1000));
  }
}

function formData(form) {
  const d = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    d[el.name] = el.type === "checkbox" ? el.checked : el.value;
  }
  return d;
}

// ------------------------------------------------------------------ clients
function renderClients() {
  const st = app.state;
  $("#clients tbody").innerHTML = st.clients.length ? st.clients.map((c) => `<tr>
    <td><strong>${esc(c.name)}</strong></td><td>${esc(c.hosts)}</td><td>${esc(c.hostgroups)}</td><td>${esc(c.pollers)}</td><td>${esc(c.services)}</td><td>${esc(c.exclude)}</td><td>${esc(c.mode)}</td>
    <td><button data-apply="${esc(c.name)}">Afficher</button> <button data-edit-client="${esc(c.name)}" class="ghost">Modifier</button>
    <button data-del-client="${esc(c.name)}" class="ghost">Supprimer</button></td></tr>`).join("")
    : '<tr><td colspan="8" class="muted">Aucun client enregistré.</td></tr>';
  $("#prefixes").innerHTML = st.prefixes.length
    ? st.prefixes.map(([p, n]) => `<span class="chip" data-prefix="${esc(p)}">${esc(p)} <span class="muted">${n}</span></span>`).join("")
    : '<span class="muted">Aucun préfixe récurrent (chargez un inventaire).</span>';
  const lk = app.lookup;
  const groups = lk ? lk.hostgroups.map((g) => [g, ""]) : (st.hostgroups || []);
  const pollers = lk ? lk.pollers.map((p) => [p, ""]) : (st.pollers || []);
  const chip = (kind, [v, n]) => `<span class="chip" data-${kind}="${esc(v)}">${esc(v)}${n !== "" ? ` <span class="muted">${n}</span>` : ""}</span>`;
  const gp = lk ? (lk.hostgroup_prefixes || []).map(([p, n]) => chip("group", [p + "*", n])).join("") : "";
  $("#groups").innerHTML = (gp ? `<div class="muted small">Préfixes :</div>${gp}<div class="muted small" style="width:100%">Groupes :</div>` : "")
    + (groups.length ? groups.map((g) => chip("group", g)).join("") : '<span class="muted">Aucun groupe connu.</span>');
  $("#pollers").innerHTML = pollers.length ? pollers.map((p) => chip("poller", p)).join("") : '<span class="muted">Aucun collecteur connu.</span>';
}

function applyScope(s) {
  Object.assign(app.scope, s);
  $("#scope-client").value = app.scope.client;
  $("#scope-hosts").value = app.scope.hosts;
  $("#scope-hostgroups").value = app.scope.hostgroups || "";
  $("#scope-pollers").value = app.scope.pollers || "";
  $("#scope-services").value = app.scope.services;
  $("#scope-exclude").value = app.scope.exclude;
  $("#scope-mode").value = app.scope.mode;
  for (const t of Object.values(app.tables)) { t.page = 1; if (t.sel) t.sel.clear(); }
  try { localStorage.setItem("scope", JSON.stringify(app.scope)); } catch (e) { /* stockage indisponible */ }
}

// ------------------------------------------------------------------ événements
function bind() {
  $("#tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-view]");
    if (b) show(b.dataset.view);
  });
  document.addEventListener("click", (e) => {
    const g = e.target.closest("[data-goto]");
    if (g) { e.preventDefault(); show(g.dataset.goto); }
  });
  $("#scope").addEventListener("submit", (e) => {
    e.preventDefault();
    applyScope({ client: "", hosts: $("#scope-hosts").value.trim(), hostgroups: $("#scope-hostgroups").value.trim(),
      pollers: $("#scope-pollers").value.trim(), services: $("#scope-services").value.trim(),
      exclude: $("#scope-exclude").value.trim(), mode: $("#scope-mode").value });
    refresh();
  });
  $("#scope-client").addEventListener("change", (e) => {
    const c = app.state.clients.find((x) => x.name === e.target.value);
    applyScope(c ? scopeFromClient(c) : { ...EMPTY_SCOPE });
    refresh();
  });
  $("#scope-save").addEventListener("click", async () => {
    const f = {};
    for (const k of SCOPE_FIELDS) f[k] = $(`#scope-${k}`).value.trim();
    if (!scopeActive(f)) return toast("Renseignez au moins un motif (hôtes, groupes, collecteur, services ou exclusion).");
    const name = prompt("Nom du client pour ce filtre :", f.hosts || f.hostgroups || f.pollers);
    if (!name) return;
    const c = { name, ...f, mode: $("#scope-mode").value };
    await post("/api/clients", c);
    await loadState();
    applyScope(scopeFromClient(c));
    toast(`Client « ${name} » enregistré`);
    refresh();
  });

  const grid = $("#grid");
  grid.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-detail]");
    if (a) { e.preventDefault(); e.stopPropagation(); openDetail(app.view, a.dataset.detail); }
  }, true);
  $("#drawer-close").addEventListener("click", () => $("#drawer").classList.add("hidden"));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#drawer").classList.add("hidden"); });
  $("#drawer-body").addEventListener("change", async (e) => {
    if (e.target.dataset.edit === undefined) return;
    await saveDecision(e.target);
  });
  grid.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-untpl],button[data-retpl]");
    if (b) { e.preventDefault(); toggleTemplate(b); }
  });
  grid.addEventListener("click", (e) => {
    const th = e.target.closest("thead tr:first-child th");
    if (!th || th.classList.contains("sel")) return;
    const ts = tstate(), col = th.dataset.col;
    ts.dir = ts.sort === col && ts.dir === "asc" ? "desc" : "asc";
    ts.sort = col; ts.page = 1;
    loadTable();
  });
  grid.addEventListener("input", (e) => {
    const f = e.target.dataset.filter;
    if (f === undefined || e.target.tagName !== "INPUT") return;
    const ts = tstate(); ts.filters[f] = e.target.value; ts.page = 1;
    debounced(loadTable);
  });
  grid.addEventListener("change", (e) => {
    if (e.target.dataset.edit !== undefined) return saveDecision(e.target);
    const ts = tstate();
    if (e.target.dataset.sel !== undefined) {
      if (e.target.checked) ts.sel.add(e.target.dataset.sel); else ts.sel.delete(e.target.dataset.sel);
      e.target.closest("tr").classList.toggle("selected", e.target.checked);
      return updateBulk();
    }
    if (e.target.dataset.selPage !== undefined) {
      for (const box of $$("input[data-sel]", grid)) {
        box.checked = e.target.checked;
        if (box.checked) ts.sel.add(box.dataset.sel); else ts.sel.delete(box.dataset.sel);
        box.closest("tr").classList.toggle("selected", box.checked);
      }
      return updateBulk();
    }
    const f = e.target.dataset.filter;
    if (f !== undefined && e.target.tagName === "SELECT") {
      const ts = tstate(); ts.filters[f] = e.target.value; ts.page = 1; loadTable();
    }
  });
  $("#bulk-field").addEventListener("change", renderBulkValue);
  $("#bulk-apply").addEventListener("click", applyBulk);
  $("#bulk-clear").addEventListener("click", () => { tstate().sel.clear(); loadTable(); });
  $("#bulk-all").addEventListener("click", async () => {
    const ts = tstate();
    const { keys } = await api(`/api/table/${app.view}/keys?` + tableQuery(ts));
    keys.forEach((k) => ts.sel.add(k));
    loadTable();
  });
  $("#search").addEventListener("input", (e) => { const ts = tstate(); ts.q = e.target.value; ts.page = 1; debounced(loadTable); });
  $("#size").addEventListener("change", (e) => { const ts = tstate(); ts.size = +e.target.value; ts.page = 1; loadTable(); });
  $("#clear-filters").addEventListener("click", () => { const ts = tstate(); ts.filters = {}; ts.q = ""; ts.page = 1; loadTable(); });
  $("#pager").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-page]");
    if (b) { tstate().page = +b.dataset.page; loadTable(); }
  });

  $("#form-centreon").addEventListener("submit", async (e) => {
    e.preventDefault();
    const d = formData(e.target);
    if (d.scope_only) {
      const s = app.scope, c = s.client ? (app.state.clients.find((x) => x.name === s.client) || {}) : s;
      d.scope = { name: s.client || "", mode: c.mode || "et" };
      for (const k of SCOPE_FIELDS) d.scope[k] = c[k] || "";
    }
    try {
      const { job } = await post("/api/jobs/centreon", d);
      e.target.password.value = "";
      followJob(job, $("#status-centreon"));
    } catch (err) { toast(err.message); }
  });
  $("#form-zabbix").addEventListener("submit", async (e) => {
    e.preventDefault();
    const d = formData(e.target);
    try {
      const { job } = await post("/api/jobs/zabbix", d);
      e.target.password.value = ""; e.target.token.value = "";
      followJob(job, $("#status-zabbix"));
    } catch (err) { toast(err.message); }
  });
  $("#lookup").addEventListener("click", async () => {
    const d = formData($("#form-centreon"));
    if (!d.url || !d.user || !d.password) return toast("Renseignez l'URL, l'utilisateur et le mot de passe Centreon.");
    try {
      app.lookup = await post("/api/centreon/lookup", d);
      renderClients();
      toast(`${app.lookup.hostgroups.length} groupes d'hôtes, ${app.lookup.pollers.length} collecteurs : voir l'onglet Clients`);
      show("clients");
    } catch (err) { toast(err.message); }
  });
  $("#forget-zabbix").addEventListener("click", async () => {
    await api("/api/zabbix", { method: "DELETE" });
    await loadState(); toast("Inventaire Zabbix oublié");
  });
  $("#form-clapi").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const r = await api("/api/import/clapi", { method: "POST", body: fd });
      toast(`Import : ${r.hosts} hôtes, ${r.services} services`);
      await loadState();
    } catch (err) { toast(err.message); }
  });
  $("#demo").addEventListener("click", async () => {
    const r = await post("/api/import/demo", {});
    toast(`Démonstration chargée : ${r.hosts} hôtes, ${r.services} services`);
    await loadState(); show("dashboard");
  });

  const fcl = $("#form-client");
  fcl.addEventListener("submit", async (e) => {
    e.preventDefault();
    const d = formData(fcl);
    try {
      await post("/api/clients?old=" + encodeURIComponent(d.old || ""), d);
      fcl.reset(); await loadState(); toast(`Client « ${d.name} » enregistré`);
    } catch (err) { toast(err.message); }
  });
  $("#view-clients").addEventListener("click", async (e) => {
    const t = e.target.closest("[data-apply],[data-edit-client],[data-del-client],[data-prefix],[data-group],[data-poller]");
    if (!t) return;
    const find = (n) => app.state.clients.find((c) => c.name === n);
    if (t.dataset.apply) {
      const c = find(t.dataset.apply);
      applyScope(scopeFromClient(c));
      show("dashboard");
    } else if (t.dataset.editClient) {
      const c = find(t.dataset.editClient);
      for (const k of ["name", ...SCOPE_FIELDS, "mode"]) fcl[k].value = c[k] || "";
      fcl.old.value = c.name; fcl.name.focus();
    } else if (t.dataset.delClient) {
      if (!confirm(`Supprimer le client « ${t.dataset.delClient} » ?`)) return;
      await api("/api/clients/" + encodeURIComponent(t.dataset.delClient), { method: "DELETE" });
      if (app.scope.client === t.dataset.delClient) applyScope({ ...EMPTY_SCOPE });
      await loadState();
    } else if (t.dataset.group || t.dataset.poller) {
      const field = t.dataset.group ? fcl.hostgroups : fcl.pollers, v = t.dataset.group || t.dataset.poller;
      const cur = field.value.split(",").map((x) => x.trim()).filter(Boolean);
      if (!cur.includes(v)) field.value = [...cur, v].join(", ");
      if (!fcl.name.value) fcl.name.value = v.replace(/\*$/, "");
      field.focus();
    } else if (t.dataset.prefix) {
      fcl.reset(); fcl.name.value = t.dataset.prefix; fcl.hosts.value = t.dataset.prefix; fcl.name.focus();
    }
  });
}

(async function init() {
  bind();
  bindAutocomplete();
  try {
    const saved = JSON.parse(localStorage.getItem("scope") || "null");
    if (saved) Object.assign(app.scope, saved);
  } catch (e) { /* stockage indisponible */ }
  await loadState();
  if (app.scope.client && !app.state.clients.some((c) => c.name === app.scope.client)) app.scope.client = "";
  applyScope(app.scope);
  let view = "dashboard";
  try { view = localStorage.getItem("view") || "dashboard"; } catch (e) { /* stockage indisponible */ }
  if (!app.state.centreon) view = "sources";
  show(view);
  window.addEventListener("resize", () => {
    const grid = $("#grid"), n = grid.querySelectorAll("thead tr:first-child th.frz").length;
    if (n) freezeColumns(grid, n);
  });
})();
