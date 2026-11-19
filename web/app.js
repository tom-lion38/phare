"use strict";
/* Phare : interface. JavaScript sans dépendance. Lit /api/vue toutes les 5 s. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nb = (n) => (n ?? 0).toLocaleString("fr-FR");
const compact = (n) => (n >= 10000 ? (n / 1000).toFixed(n >= 100000 ? 0 : 1).replace(".", ",") + " k" : nb(n));
const go = (o) => (o / 1073741824).toFixed(o >= 10737418240 ? 0 : 1).replace(".", ",") + " Go";
const mo = (o) => Math.round(o / 1048576) + " Mo";

let V = null;                 // dernière vue reçue
let vueActive = "sites";
let filtre = localStorage.getItem("phare-filtre") || "site";
const occupes = new Set();    // clés de groupes / CT en cours d'action

const TITRES = {
  sites: ["Sites", "allumer, éteindre, regarder qui passe"],
  caddy: ["Caddy", "les domaines et où ils mènent"],
  nouveau: ["Nouveau <em>site</em>", "un CT, un domaine, en ligne"],
  cts: ["Conteneurs", "tous les CT de l'hôte Proxmox"],
  journal: ["Journal", "tout ce que Phare a fait"],
};

/* ------------------------------------------------------------------ API */
async function api(chemin, { methode = "GET", corps, brut, type } = {}) {
  const opts = { method: methode, headers: {} };
  if (methode !== "GET") opts.headers["X-Phare"] = "1";
  if (brut) { opts.body = brut; opts.headers["Content-Type"] = type || "application/octet-stream"; }
  else if (corps !== undefined) { opts.body = JSON.stringify(corps); opts.headers["Content-Type"] = "application/json"; }
  const r = await fetch(chemin, opts);
  let d;
  try { d = await r.json(); } catch { throw new Error(`Réponse illisible (${r.status})`); }
  if (!r.ok || d.ok === false) throw new Error(d.erreur || `Erreur ${r.status}`);
  return d;
}

function toast(texte, ko = false) {
  const t = document.createElement("div");
  t.className = "toast" + (ko ? " ko" : "");
  t.textContent = texte;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), ko ? 9000 : 4500);
}

async function action(cle, fn, succes) {
  occupes.add(cle); rendre();
  try { await fn(); if (succes) toast(succes); }
  catch (e) { toast(e.message, true); }
  finally { occupes.delete(cle); await charger(); }
}

/* ------------------------------------------------------------------ chargement */
async function charger() {
  try {
    V = await api("/api/vue");
    rendre();
  } catch (e) {
    $("#alertes").innerHTML = `<div class="alerte">Phare ne répond pas : ${esc(e.message)}</div>`;
  }
}

function rendre() {
  if (!V) return;
  rendreTete();
  if (vueActive === "sites") rendreSites();
  if (vueActive === "caddy") rendreCaddy();
  if (vueActive === "cts") rendreCts();
  if (vueActive === "nouveau") preparerNouveau();
}

function rendreTete() {
  const n = V.noeud || {};
  const cpu = Math.round((n.cpu || 0) * 100), ram = n.maxmem ? Math.round(n.mem / n.maxmem * 100) : 0;
  const allumes = V.invites.filter((i) => i.etat === "running").length;
  $("#jauges").innerHTML = `
    <div class="jauge"><small>CPU <b>${cpu} %</b></small><div class="barre ${cpu > 80 ? "chaud" : ""}"><i style="width:${cpu}%"></i></div></div>
    <div class="jauge"><small>RAM <b>${n.maxmem ? go(n.mem) + " / " + go(n.maxmem) : "-"}</b></small><div class="barre ${ram > 85 ? "chaud" : ""}"><i style="width:${ram}%"></i></div></div>
    <div class="jauge"><small>Invités <b>${allumes} / ${V.invites.length}</b></small><div class="barre"><i style="width:${V.invites.length ? allumes / V.invites.length * 100 : 0}%"></i></div></div>`;
  const al = [];
  if (V.demo) al.push(`<div class="alerte demo">Mode démo : rien n'est envoyé au homelab.</div>`);
  for (const [k, m] of Object.entries(V.erreurs || {})) al.push(`<div class="alerte"><b>${esc(k)}</b> : ${esc(m)}</div>`);
  $("#alertes").innerHTML = al.join("");
  const cfg = V.config || {};
  if ($("#ct-caddy")) $("#ct-caddy").textContent = cfg.ct_caddy ? ` (CT ${cfg.ct_caddy})` : "";
  $$(".exemple-domaine").forEach((i) => { i.placeholder = "monsite." + (cfg.domaine_exemple || "exemple.fr"); });
  if (vueActive === "cts" && cfg.noeud) $("#sur-titre").textContent = `tous les CT du nœud ${cfg.noeud}`;
  const age = Math.round(Date.now() / 1000 - (V.maj?.etat || 0));
  $("#fraicheur").textContent = V.maj?.etat ? `état lu il y a ${age} s` : "-";
}

/* ------------------------------------------------------------------ SITES */
function etatGroupe(g) {
  const ctOn = !g.ct || g.ct.etat === "running";
  const sante = g.sante;
  if (g.maintenance) return { lampe: "maint", libelle: "En maintenance", allume: false };
  if (!ctOn) return { lampe: "", libelle: "CT éteint", allume: false };
  if (g.unite_etat && g.unite_etat !== "running" && g.unite_etat !== "active") return { lampe: "panne", libelle: `Service ${g.unite_etat}`, allume: false, panne: true };
  if (sante && !sante.ok) return { lampe: "panne", libelle: sante.code ? `Erreur ${sante.code}` : sante.erreur === "certificat" ? "Pas de certificat HTTPS (DNS ?)" : "Ne répond pas", allume: true, panne: true };
  return { lampe: "on", libelle: "En ligne", allume: true };
}

function etincelle(serie) {
  const w = 130, h = 34, max = Math.max(1, ...serie);
  const pts = serie.map((v, i) => [i / (serie.length - 1) * w, h - 2 - v / max * (h - 6)]);
  const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join("");
  return `<svg class="etincelle" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-label="Visiteurs sur 30 jours">
    <path class="aire" d="${d}L${w} ${h}L0 ${h}Z"/><path class="trait" d="${d}"/></svg>`;
}

function carteSite(g, i) {
  const e = etatGroupe(g);
  const ct = g.ct;
  const s = g.stats || {};
  const occ = occupes.has(g.cle) || (ct && occupes.has("ct" + ct.id));
  const alias = g.domaines.filter((d) => d.nom !== g.nom)
    .map((d) => `<span class="${d.maintenance ? "m" : ""}" title="${d.maintenance ? "en maintenance" : ""}">${esc(d.nom)}</span>`).join("");
  const sante = g.sante?.ok ? `<span class="etiquette ok">${g.sante.ms} ms</span>` : g.sante ? `<span class="etiquette ko" title="${esc(g.sante.detail || "")}">${esc(g.sante.code || g.sante.erreur || "hors ligne")}</span>` : "";
  const unite = g.unite ? `<span class="etiquette ${["running", "active"].includes(g.unite_etat) ? "ok" : "ko"}">${esc(g.unite)}</span>` : (ct && ct.etat === "running" ? `<span class="etiquette">service ?</span>` : "");
  const pilotable = ct && !ct.protege && ct.type === "lxc";
  const lien = g.domaines.find((d) => !d.lan) ? `https://${g.nom}` : `https://${g.nom}`;
  return `<article class="carte ${e.allume ? "allume" : "eteint"} ${e.panne ? "panne" : ""} ${occ ? "occupe" : ""}" style="--i:${i}" data-cle="${esc(g.cle)}">
    <div class="carte-tete">
      <span class="lampe ${e.lampe}" title="${esc(e.libelle)}"></span>
      <div class="carte-titre">
        <h3><a href="${esc(lien)}" target="_blank" rel="noopener">${esc(g.nom)}</a></h3>
        ${alias ? `<div class="alias">${alias}</div>` : ""}
      </div>
    </div>
    <div class="meta">
      ${ct ? `<span>CT <b>${esc(ct.id)}</b> ${esc(ct.nom)}</span>` : `<span>hors CT</span>`}
      <span>${esc(g.ip || "-")}:${esc(g.port || "")}</span>
      ${unite}${sante}
      <span class="etiquette ${e.panne ? "ko" : e.allume ? "ok" : g.maintenance ? "bleu" : ""}">${esc(e.libelle)}</span>
    </div>
    <div class="stats">
      <div class="stat"><b>${compact(s.aujourdhui)}</b><small>auj.</small></div>
      <div class="stat"><b>${compact(s.j7)}</b><small>7 j</small></div>
      <div class="stat"><b>${compact(s.j30)}</b><small>30 j</small></div>
      <div class="stat" title="${s.depuis ? "depuis le " + s.depuis.replace(/(\d{4})(\d\d)(\d\d)/, "$3/$2/$1") : ""}"><b>${compact(s.total)}</b><small>total</small></div>
      ${etincelle(s.serie || [0, 0])}
    </div>
    <div class="actions">
      ${g.categorie !== "infra" || pilotable ? `<button class="interrupteur" role="switch" aria-checked="${!g.maintenance && e.allume}" data-act="basculer" title="Éteindre le site affiche une page de maintenance, le CT reste allumé">
        <span class="piste"></span>Site ${g.maintenance || !e.allume ? "éteint" : "allumé"}</button>` : `<span class="pastille prot">infra protégée</span>`}
      <span class="espace"></span>
      ${pilotable ? `<button class="btn icone" data-act="ct" title="${ct.etat === "running" ? "Éteindre le CT" : "Allumer le CT"}">⏻</button>` : ""}
      <div class="menu"><button class="btn icone" data-act="menu" aria-label="Plus d'actions">⋯</button></div>
    </div>
  </article>`;
}

function rendreSites() {
  const groupes = V.groupes.filter((g) => filtre === "tous" || g.categorie === filtre);
  const q = ($("#recherche").value || "").toLowerCase();
  const vis = groupes.filter((g) => !q || JSON.stringify([g.nom, g.domaines.map((d) => d.nom), g.ct?.nom, g.ip]).toLowerCase().includes(q));
  const sites = V.groupes.filter((g) => g.categorie === "site");
  const enLigne = sites.filter((g) => etatGroupe(g).lampe === "on").length;
  const somme = (k) => sites.reduce((a, g) => a + (g.stats?.[k] || 0), 0);
  $("#bandeau").innerHTML = `
    <div><b>${enLigne}<span class="petit"> / ${sites.length}</span></b><small>sites en ligne</small></div>
    <div><b>${nb(somme("aujourdhui"))}</b><small>visiteurs aujourd'hui</small></div>
    <div><b>${nb(somme("j7"))}</b><small>sur 7 jours</small></div>
    <div><b>${nb(somme("j30"))}</b><small>sur 30 jours · ${nb(somme("total"))} au total</small></div>`;
  $$("#filtres button").forEach((b) => b.classList.toggle("actif", b.dataset.f === filtre));
  const html = vis.map(carteSite).join("") || `<p class="vide">Aucun site ici.</p>`;
  if (html !== $("#grille").dataset.html) { $("#grille").innerHTML = html; $("#grille").dataset.html = html; }
  const libres = V.cts_libres || [];
  $("#titre-libres").hidden = !libres.length || filtre === "infra";
  $("#libres").innerHTML = filtre === "infra" ? "" : libres.map((c, i) => `
    <article class="carte ${c.etat === "running" ? "allume" : "eteint"} ${occupes.has("ct" + c.id) ? "occupe" : ""}" style="--i:${i}" data-ct="${esc(c.id)}">
      <div class="carte-tete"><span class="lampe ${c.etat === "running" ? "on" : ""}"></span>
        <div class="carte-titre"><h3>${esc(c.nom)}</h3><div class="meta"><span>CT <b>${esc(c.id)}</b></span><span>${esc(c.ip || "-")}</span></div></div></div>
      <div class="actions">
        <button class="btn" data-act="domaine-ct">＋ Domaine</button>
        <button class="btn" data-act="deployer-ct">Déployer</button>
        <span class="espace"></span>
        <button class="btn icone" data-act="ct-libre" title="${c.etat === "running" ? "Éteindre" : "Allumer"} le CT">⏻</button>
      </div>
    </article>`).join("");
}

function menuSite(bouton, g) {
  fermerMenus();
  const ct = g.ct, pilotable = ct && !ct.protege && ct.type === "lxc";
  const l = document.createElement("div");
  l.className = "menu-liste";
  l.innerHTML = `
    ${pilotable && g.unite ? `<button data-m="redemarrer">Redémarrer ${esc(g.unite)}</button>` : ""}
    <button data-m="destination">Changer la destination</button>
    <button data-m="domaine">Ajouter un domaine ici</button>
    ${pilotable ? `<hr><button data-m="deployer">Déployer une archive…</button>
    <button data-m="snapshot">Snapshot maintenant</button>
    <button data-m="snapshots">Snapshots du CT…</button>
    <hr><button data-m="reboot">Redémarrer le CT</button>` : ""}
    <hr><button data-m="ouvrir">Ouvrir le site</button>`;
  bouton.parentElement.append(l);
  l.addEventListener("click", (ev) => {
    const m = ev.target.closest("button")?.dataset.m;
    if (!m) return;
    fermerMenus();
    if (m === "redemarrer") action(g.cle, () => api("/api/site/redemarrer", { methode: "POST", corps: { cle: g.cle } }), "Service redémarré");
    if (m === "destination") tiroirDestination(g);
    if (m === "domaine") tiroirDomaine({ ct: ct?.id, ip: g.ip, port: g.port });
    if (m === "deployer") tiroirDeployer(ct);
    if (m === "snapshot") action("ct" + ct.id, () => api(`/api/ct/${ct.id}/snapshot`, { methode: "POST", corps: {} }), "Snapshot pris");
    if (m === "snapshots") tiroirSnapshots(ct);
    if (m === "reboot") confirmer(`Redémarrer le CT ${ct.id} (${ct.nom}) ?`, "Le site sera coupé quelques secondes.", "Redémarrer",
      () => action("ct" + ct.id, () => api(`/api/ct/${ct.id}/reboot`, { methode: "POST", corps: {} }), "CT redémarré"));
    if (m === "ouvrir") window.open("https://" + g.nom, "_blank", "noopener");
  });
}
function fermerMenus() { $$(".menu-liste").forEach((m) => m.remove()); }
document.addEventListener("click", (e) => { if (!e.target.closest(".menu")) fermerMenus(); });

$("#grille").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-act]");
  if (!b) return;
  const g = V.groupes.find((x) => x.cle === b.closest(".carte").dataset.cle);
  if (!g) return;
  const act = b.dataset.act;
  if (act === "menu") return menuSite(b, g);
  if (act === "basculer") {
    const eteindre = b.getAttribute("aria-checked") === "true";
    if (eteindre) {
      confirmer(`Éteindre ${g.nom} ?`,
        `${g.unite ? `Le service ${g.unite} s'arrête dans le CT ${g.ct?.id}. ` : ""}Les visiteurs voient la page de maintenance. Le CT reste allumé.`,
        "Éteindre le site", () => action(g.cle, () => api("/api/site/eteindre", { methode: "POST", corps: { cle: g.cle } }), `${g.nom} éteint`));
    } else {
      action(g.cle, () => api("/api/site/allumer", { methode: "POST", corps: { cle: g.cle } }), `${g.nom} allumé`);
    }
  }
  if (act === "ct") basculerCt(g.ct);
});
$("#libres").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-act]");
  if (!b) return;
  const ct = V.cts_libres.find((c) => c.id === b.closest(".carte").dataset.ct);
  if (b.dataset.act === "ct-libre") basculerCt(ct);
  if (b.dataset.act === "domaine-ct") tiroirDomaine({ ct: ct.id, ip: ct.ip });
  if (b.dataset.act === "deployer-ct") tiroirDeployer(ct);
});

function basculerCt(ct) {
  if (!ct) return;
  if (ct.etat === "running") {
    confirmer(`Éteindre le CT ${ct.id} (${ct.nom}) ?`, "Tout ce qui tourne dedans s'arrête. Les visiteurs verront une erreur tant qu'il est éteint (utilise plutôt « Site éteint » pour une page de maintenance).",
      "Éteindre le CT", () => action("ct" + ct.id, () => api(`/api/ct/${ct.id}/shutdown`, { methode: "POST", corps: {} }), `CT ${ct.id} éteint`));
  } else {
    action("ct" + ct.id, () => api(`/api/ct/${ct.id}/start`, { methode: "POST", corps: {} }), `CT ${ct.id} allumé`);
  }
}

$("#filtres").addEventListener("click", (e) => {
  const f = e.target.closest("button")?.dataset.f;
  if (!f) return;
  filtre = f; localStorage.setItem("phare-filtre", f); rendreSites();
});
$("#recherche").addEventListener("input", rendreSites);

/* ------------------------------------------------------------------ tiroir */
function ouvrirTiroir(html) {
  $("#tiroir-contenu").onclick = null;
  $("#tiroir-contenu").innerHTML = html;
  $("#tiroir").hidden = false; $("#voile").hidden = false;
  setTimeout(() => $("#tiroir input:not([type=hidden]), #tiroir button.primaire")?.focus(), 50);
}
function fermerTiroir() { $("#tiroir").hidden = true; $("#voile").hidden = true; $("#tiroir-contenu").innerHTML = ""; }
$("#tiroir-fermer").onclick = fermerTiroir;
$("#voile").onclick = fermerTiroir;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { fermerTiroir(); fermerMenus(); } });

function confirmer(titre, texte, bouton, fn, danger = true) {
  ouvrirTiroir(`<h2>${esc(titre)}</h2><p>${esc(texte)}</p>
    <div class="ligne-btn"><button class="fantome" data-x="non">Annuler</button><button class="${danger ? "danger" : "primaire"}" data-x="oui">${esc(bouton)}</button></div>`);
  $("#tiroir-contenu").onclick = (e) => {
    const x = e.target.closest("[data-x]")?.dataset.x;
    if (!x) return;
    fermerTiroir();
    if (x === "oui") fn();
  };
}

/* action lourde : le nom doit être retapé (le serveur le revérifie) */
function confirmerNom(titre, texte, bouton, attendu, fn) {
  ouvrirTiroir(`<h2>${esc(titre)}</h2><p>${esc(texte)}</p>
    <form id="f-conf"><label><span>Retape <b class="mono">${esc(attendu)}</b> pour confirmer</span><input name="c" class="mono" autocomplete="off" spellcheck="false"></label>
    <div class="ligne-btn"><button type="button" class="fantome" onclick="fermerTiroir()">Annuler</button><button class="danger" disabled>${esc(bouton)}</button></div></form>`);
  const f = $("#f-conf"), b = $("button.danger", f);
  f.c.oninput = () => { b.disabled = f.c.value.trim() !== attendu; };
  f.onsubmit = (e) => {
    e.preventDefault();
    const c = f.c.value.trim();
    if (c !== attendu) return;
    fermerTiroir();
    fn(c);
  };
}

function optionsCt(choisi) {
  return V.invites.filter((i) => i.type === "lxc" && i.ip)
    .map((i) => `<option value="${esc(i.id)}" data-ip="${esc(i.ip)}" ${i.id === String(choisi) ? "selected" : ""}>${esc(i.id)} · ${esc(i.nom)} · ${esc(i.ip)}${i.etat !== "running" ? " (éteint)" : ""}</option>`).join("");
}

function portsDe(ctid) {
  const s = V.services?.[ctid];
  return (s?.ecoute || []).filter((e) => e.port !== 22);
}

/* champ « CT » qui remplit l'IP et propose les ports qui écoutent */
function brancherChoixCt(form) {
  const sel = form.ct, ip = form.ip, port = form.port, zone = $(".ports", form);
  const maj = (premier) => {
    const opt = sel.selectedOptions[0];
    if (opt?.dataset.ip && !premier) ip.value = opt.dataset.ip;
    const ports = portsDe(sel.value);
    zone.innerHTML = ports.length
      ? ports.map((p) => `<button type="button" class="btn" data-port="${p.port}">${p.port} · ${esc(p.processus || p.unite || "?")}</button>`).join("")
      : `<small class="vide">${sel.value ? "aucun port détecté (CT éteint ou pas encore lu)" : ""}</small>`;
    if (!premier && ports.length && !ports.some((p) => String(p.port) === port.value)) port.value = ports[0].port;
  };
  sel.onchange = () => maj(false);
  zone.onclick = (e) => { const p = e.target.closest("[data-port]")?.dataset.port; if (p) port.value = p; };
  maj(true);
}

function tiroirDomaine({ ct = "", ip = "", port = 80 } = {}) {
  ouvrirTiroir(`<h2>Ajouter un domaine</h2><p>Caddy obtient le certificat HTTPS tout seul dès que le DNS pointe vers ta box.</p>
    <form id="f-dom">
      <label>Domaine<input name="domaine" required placeholder="monsite.${esc(V?.config?.domaine_exemple || "exemple.fr")}" class="mono"></label>
      <label>CT de destination<select name="ct"><option value="">- IP saisie à la main -</option>${optionsCt(ct)}</select></label>
      <div class="champs deux"><label>IP<input name="ip" required class="mono" value="${esc(ip)}"></label><label>Port<input name="port" required inputmode="numeric" class="mono" value="${esc(port || 80)}"></label></div>
      <div class="ports"></div>
      <div class="dns" id="dns-zone"><b>DNS</b><span class="vide">Tape le domaine pour vérifier.</span></div>
      <div class="ligne-btn"><button type="button" class="fantome" onclick="fermerTiroir()">Annuler</button><button class="primaire">Ajouter dans Caddy</button></div>
    </form>`);
  const f = $("#f-dom");
  brancherChoixCt(f);
  let minuteur;
  f.domaine.oninput = () => { clearTimeout(minuteur); minuteur = setTimeout(() => verifierDns(f.domaine.value, $("#dns-zone")), 600); };
  f.onsubmit = (e) => {
    e.preventDefault();
    const corps = { domaine: f.domaine.value.trim(), ip: f.ip.value.trim(), port: +f.port.value };
    fermerTiroir();
    action("caddy", () => api("/api/caddy/ajouter", { methode: "POST", corps }), `${corps.domaine} ajouté dans Caddy`);
  };
}

async function verifierDns(domaine, zone) {
  domaine = (domaine || "").trim().toLowerCase();
  if (!/^[a-z0-9.-]+\.[a-z]{2,}$/.test(domaine)) { zone.innerHTML = `<b>DNS</b><span class="vide">Tape le domaine pour vérifier.</span>`; return; }
  zone.innerHTML = `<b>DNS</b><span class="vide">Vérification…</span>`;
  try {
    const d = await api("/api/dns?d=" + encodeURIComponent(domaine));
    if (!d.attendu) { zone.innerHTML = `<b>DNS</b><span>Il pointe vers ${esc(d.ips.join(", ") || "rien pour l'instant")}. Règle PHARE_IP_PUBLIQUE dans /etc/phare/phare.env pour que Phare compare avec ta box.</span>`; return; }
    zone.innerHTML = d.pointe
      ? `<b>DNS OK</b><span>${esc(domaine)} pointe déjà vers ${esc(d.attendu)}.</span>`
      : `<b>DNS à créer chez ton registrar</b><span>${d.ips.length ? `Il pointe vers ${esc(d.ips.join(", "))}, pas vers ta box.` : "Le domaine ne répond pas encore."} Crée :</span>
         <code>${esc(domaine)}.  A  ${esc(d.attendu)}</code>
         <small class="vide">L'automatisation par clé API du registrar viendra plus tard ; Phare revérifie quand tu rouvres ce panneau.</small>`;
  } catch (e) { zone.innerHTML = `<b>DNS</b><span>${esc(e.message)}</span>`; }
}

function tiroirDestination(g) {
  ouvrirTiroir(`<h2>Changer la destination</h2><p>${g.domaines.map((d) => esc(d.nom)).join(", ")}<br>actuellement vers <b class="mono">${esc(g.cle)}</b></p>
    <form id="f-dest">
      <label>CT de destination<select name="ct"><option value="">- IP saisie à la main -</option>${optionsCt(g.ct?.id)}</select></label>
      <div class="champs deux"><label>IP<input name="ip" required class="mono" value="${esc(g.ip || "")}"></label><label>Port<input name="port" required inputmode="numeric" class="mono" value="${esc(g.port || 80)}"></label></div>
      <div class="ports"></div>
      <label><span>Retape <b class="mono">${esc(g.nom)}</b> pour confirmer</span><input name="confirmation" required class="mono" autocomplete="off" spellcheck="false"></label>
      <div class="ligne-btn"><button type="button" class="fantome" onclick="fermerTiroir()">Annuler</button><button class="primaire">Appliquer</button></div>
    </form>
    <hr style="border:0;border-top:1px solid var(--ligne);margin:28px 0 16px">
    <h3 style="font:500 18px var(--serif);margin:0 0 10px">Domaines de ce site</h3>
    <ul class="liste-snap">${g.domaines.map((d) => `<li><span class="mono">${esc(d.nom)}${d.maintenance ? ' <span class="pastille maint">maintenance</span>' : ""}</span>
      <span><button class="btn" data-maint="${esc(d.nom)}" data-v="${d.maintenance ? 0 : 1}">${d.maintenance ? "Rallumer" : "Maintenance"}</button>
      <button class="danger" data-retirer="${esc(d.nom)}">Retirer</button></span></li>`).join("")}</ul>`);
  const f = $("#f-dest");
  brancherChoixCt(f);
  f.onsubmit = (e) => {
    e.preventDefault();
    const corps = { cle: g.cle, ip: f.ip.value.trim(), port: +f.port.value, confirmation: f.confirmation.value.trim() };
    if (corps.confirmation !== g.nom) { toast(`Retape exactement ${g.nom}`, true); return; }
    fermerTiroir();
    action(g.cle, () => api("/api/caddy/amont", { methode: "POST", corps }), "Destination changée");
  };
  $("#tiroir-contenu").onclick = (e) => {
    const m = e.target.closest("[data-maint]"), r = e.target.closest("[data-retirer]");
    if (m) { fermerTiroir(); action(g.cle, () => api("/api/caddy/maintenance", { methode: "POST", corps: { domaine: m.dataset.maint, active: m.dataset.v === "1" } }), "Fait"); }
    if (r) confirmerNom(`Retirer ${r.dataset.retirer} de Caddy ?`, "Le domaine ne sera plus servi. Le Caddyfile d'avant reste dans l'historique.", "Retirer", r.dataset.retirer,
      (confirmation) => action(g.cle, () => api("/api/caddy/retirer", { methode: "POST", corps: { domaine: r.dataset.retirer, confirmation } }), "Domaine retiré"));
  };
}

function tiroirDeployer(ct) {
  ouvrirTiroir(`<h2>Déployer sur le CT ${esc(ct.id)}</h2><p>${esc(ct.nom)} · ${esc(ct.ip)}. Snapshot automatique avant, pour pouvoir revenir en arrière.</p>
    <form id="f-dep">
      <div class="choix">
        ${["statique", "node", "next"].map((t, i) => `<label class="carte-choix"><input type="radio" name="type" value="${t}" ${(ct.tags || "").includes(t) || (!i && !/node|next/.test(ct.tags || "")) ? "checked" : ""}><b>${{ statique: "Statique", node: "Node", next: "Next.js" }[t]}</b></label>`).join("")}
      </div>
      <label class="depot" id="depot-dep"><input type="file" accept=".zip,.tar.gz,.tgz" hidden><b>Glisse ton .zip</b><span>ou clique pour choisir (300 Mo max)</span></label>
      <div class="ligne-btn"><button type="button" class="fantome" onclick="fermerTiroir()">Annuler</button><button class="primaire" disabled>Déployer</button></div>
    </form>`);
  const f = $("#f-dep");
  let fichier = null;
  brancherDepot($("#depot-dep"), (fi) => { fichier = fi; $("button.primaire", f).disabled = !fi; });
  f.onsubmit = async (e) => {
    e.preventDefault();
    if (!fichier) return;
    const type = f.type.value;
    suivreJob(`Déploiement sur le CT ${ct.id}`, () => api(`/api/deployer/${ct.id}?type=${type}`, { methode: "POST", brut: fichier }));
  };
}

function brancherDepot(zone, rappel) {
  const input = $("input", zone);
  const choisir = (fi) => {
    if (!fi) return;
    if (!/\.(zip|tar\.gz|tgz)$/i.test(fi.name)) { toast("Envoie un .zip ou un .tar.gz", true); return; }
    zone.classList.add("plein");
    $("b", zone).textContent = fi.name;
    $("span", zone).textContent = (fi.size / 1048576).toFixed(1).replace(".", ",") + " Mo · prêt";
    rappel(fi);
  };
  input.onchange = () => choisir(input.files[0]);
  zone.ondragover = (e) => { e.preventDefault(); zone.classList.add("survol"); };
  zone.ondragleave = () => zone.classList.remove("survol");
  zone.ondrop = (e) => { e.preventDefault(); zone.classList.remove("survol"); choisir(e.dataTransfer.files[0]); };
}

async function tiroirSnapshots(ct) {
  ouvrirTiroir(`<h2>Snapshots du CT ${esc(ct.id)}</h2><p>Phare en prend un avant chaque déploiement et garde les 3 derniers.</p><ul class="liste-snap" id="snaps"><li class="vide">Chargement…</li></ul>`);
  try {
    const d = await api(`/api/ct/${ct.id}/snapshots`);
    $("#snaps").innerHTML = d.snapshots.map((s) => `<li><span><b class="mono">${esc(s.nom)}</b><small>${s.date ? new Date(s.date * 1000).toLocaleString("fr-FR") : ""} · ${esc(s.description)}</small></span>
      <button class="danger" data-nom="${esc(s.nom)}">Revenir ici</button></li>`).join("") || `<li class="vide">Aucun snapshot.</li>`;
    $("#snaps").onclick = (e) => {
      const nom = e.target.closest("[data-nom]")?.dataset.nom;
      if (nom) confirmerNom(`Revenir au snapshot ${nom} ?`, `Le CT ${ct.id} est arrêté puis remis exactement dans cet état. Tout ce qui a changé depuis est perdu.`, "Revenir en arrière", ct.nom,
        (confirmation) => action("ct" + ct.id, () => api(`/api/ct/${ct.id}/restaurer`, { methode: "POST", corps: { nom, confirmation } }), "Snapshot restauré"));
    };
  } catch (e) { $("#snaps").innerHTML = `<li class="vide">${esc(e.message)}</li>`; }
}

/* suivi d'une tâche longue (création, déploiement) */
async function suivreJob(titre, lanceur, ensuite) {
  ouvrirTiroir(`<h2>${esc(titre)}</h2><p id="job-etat">Envoi…</p><div class="console" id="job-console"></div>`);
  let jid;
  try { jid = (await lanceur()).job; }
  catch (e) { $("#job-etat").textContent = "Refusé"; $("#job-console").innerHTML = `<span class="ko">${esc(e.message)}</span>`; return; }
  for (;;) {
    await new Promise((r) => setTimeout(r, 1500));
    let d;
    try { d = await api("/api/job/" + jid); } catch (e) { continue; }
    if (!$("#job-console")) return;   // tiroir fermé : la tâche continue sur le serveur
    $("#job-console").innerHTML = esc(d.journal).replace(/^&gt; (.*)$/gm, '<span class="ok">&gt; $1</span>').replace(/^ERREUR : (.*)$/gm, '<span class="ko">ERREUR : $1</span>');
    $("#job-console").scrollTop = 1e9;
    $("#job-etat").textContent = d.etat === "en cours" ? "En cours… tu peux fermer, ça continue." : d.etat === "fini" ? "Terminé" : "Échec";
    if (d.etat !== "en cours") { charger(); if (d.etat === "fini" && ensuite) ensuite(); return; }
  }
}

/* ------------------------------------------------------------------ CADDY */
function rendreCaddy() {
  const lignes = [];
  const cats = { site: "Sites", appli: "Applis", infra: "Infrastructure" };
  for (const cat of ["site", "appli", "infra"]) {
    const gs = V.groupes.filter((g) => g.categorie === cat);
    if (!gs.length) continue;
    lignes.push(`<tr class="groupe-tr"><td colspan="4">${cats[cat]}</td></tr>`);
    for (const g of gs) for (const d of g.domaines) {
      lignes.push(`<tr>
        <td class="dom">${esc(d.nom)}${d.phare ? `<small>ajouté par Phare</small>` : ""}</td>
        <td class="dest">${g.ct ? `CT ${esc(g.ct.id)} · ${esc(g.ct.nom)}<br>` : ""}${esc(g.ip || "redirection")}${g.port ? ":" + esc(g.port) : ""}</td>
        <td>${d.maintenance ? `<span class="pastille maint">maintenance</span>` : `<span class="pastille on">actif</span>`} ${!d.trafic && !d.lan ? `<span class="pastille off" title="pas d'import trafic : pas de statistiques">sans stats</span>` : ""}</td>
        <td class="droite"><button class="btn" data-cle="${esc(g.cle)}">Modifier</button></td></tr>`);
    }
  }
  $("#table-caddy tbody").innerHTML = lignes.join("");
}
$("#table-caddy").addEventListener("click", (e) => {
  const cle = e.target.closest("[data-cle]")?.dataset.cle;
  const g = cle && V.groupes.find((x) => x.cle === cle);
  if (g) tiroirDestination(g);
});
$("#btn-ajout-domaine").onclick = () => tiroirDomaine();
$("#historique-caddy").addEventListener("toggle", async (e) => {
  if (!e.target.open) return;
  try {
    const d = await api("/api/caddy/sauvegardes");
    $("#liste-sauvegardes").innerHTML = d.sauvegardes.map((s) => {
      const m = s.match(/(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)(\d\d)/);
      return `<li><span>${m ? `${m[3]}/${m[2]}/${m[1]} ${m[4]}:${m[5]}:${m[6]}` : esc(s)}</span><button class="btn" data-s="${esc(s)}">Restaurer</button></li>`;
    }).join("") || `<li class="vide">Aucune sauvegarde : Phare n'a encore rien modifié.</li>`;
  } catch (er) { $("#liste-sauvegardes").innerHTML = `<li class="vide">${esc(er.message)}</li>`; }
});
$("#liste-sauvegardes").addEventListener("click", (e) => {
  const s = e.target.closest("[data-s]")?.dataset.s;
  if (s) confirmer("Restaurer cette version du Caddyfile ?", "La version actuelle est sauvegardée avant, tu pourras y revenir.", "Restaurer",
    () => action("caddy", () => api("/api/caddy/restaurer", { methode: "POST", corps: { nom: s } }), "Caddyfile restauré"));
});

/* ------------------------------------------------------------------ CONTENEURS */
function rendreCts() {
  const sv = new Set(V.sauvegarde || []);
  $("#table-cts tbody").innerHTML = V.invites.map((c) => {
    const on = c.etat === "running";
    const ram = c.maxmem ? c.mem / c.maxmem * 100 : 0;
    return `<tr>
      <td class="dom">${esc(c.id)}<small>${c.type === "qemu" ? "VM" : "CT"}</small></td>
      <td>${esc(c.nom)} ${c.protege ? `<span class="pastille prot">protégé</span>` : ""}</td>
      <td class="dest">${esc(c.ip || "-")}</td>
      <td class="dest">${on ? `<span class="mini-barre"><i style="width:${Math.min(100, (c.cpu || 0) * 100)}%"></i></span>${Math.round((c.cpu || 0) * 100)} %` : ""}<br><small>${esc(c.cpus)} cœur(s)</small></td>
      <td class="dest">${on ? `<span class="mini-barre"><i style="width:${ram}%"></i></span>` : ""}${mo(c.mem || 0)} / ${mo(c.maxmem || 0)}</td>
      <td>${sv.has(c.id) ? `<span class="pastille on">sauvegardé</span>` : `<span class="pastille off">-</span>`}</td>
      <td class="droite">${!c.protege && c.type === "lxc" ? `<button class="btn ${occupes.has("ct" + c.id) ? "" : ""}" data-ct="${esc(c.id)}" ${occupes.has("ct" + c.id) ? "disabled" : ""}>${on ? "Éteindre" : "Allumer"}</button>` : `<span class="pastille ${on ? "on" : "off"}">${on ? "allumé" : "éteint"}</span>`}</td></tr>`;
  }).join("");
}
$("#table-cts").addEventListener("click", (e) => {
  const id = e.target.closest("[data-ct]")?.dataset.ct;
  if (id) basculerCt(V.invites.find((c) => c.id === id));
});

/* ------------------------------------------------------------------ NOUVEAU */
let nouveauPret = false, archiveNouveau = null;
function preparerNouveau() {
  const f = $("#form-nouveau");
  if (!nouveauPret && V.presets && Object.keys(V.presets).length) {
    const lib = { petit: ["Petit", "maquette, vitrine"], moyen: ["Moyen", "site Node, petit trafic"], grand: ["Grand", "Next.js, boutique"] };
    $("#choix-taille").innerHTML = Object.entries(V.presets).map(([k, p], i) => `
      <label class="carte-choix"><input type="radio" name="taille" value="${k}" ${k === "petit" ? "checked" : ""}>
        <b>${lib[k]?.[0] || k}</b><div class="barres">${[1, 2, 3].map((n) => `<i class="${n <= i + 1 ? "p" : ""}"></i>`).join("")}</div>
        <span>${p.cores} cœur${p.cores > 1 ? "s" : ""} · ${p.memory >= 1024 ? p.memory / 1024 + " Go" : p.memory + " Mo"} RAM · ${p.disk} Go</span><em>${lib[k]?.[1] || ""}</em></label>`).join("");
    f.id.value = V.prochain_id || ""; f.ip.value = V.prochaine_ip || "";
    nouveauPret = true;
    majRecap();
  }
}
function majRecap() {
  const f = $("#form-nouveau");
  const nom = f.nom.value || "mon-site";
  $("#apercu-lan").textContent = nom;
  const p = V?.presets?.[f.taille?.value] || {};
  const type = f.type.value;
  $("#recap-nouveau").textContent =
    `CT ${f.id.value || "?"} « ${nom} » · Debian 13 · ${p.cores || "?"} cœur(s), ${p.memory || "?"} Mo, ${p.disk || "?"} Go · ${f.ip.value || "?"}\n` +
    `${type === "statique" ? "nginx sur le port 80" : "Node + site.service sur le port 3000"}` +
    (f.domaine.value ? `\nCaddy : ${f.domaine.value} vers ${f.ip.value}:${type === "statique" ? 80 : 3000}` : "\nPas de domaine public (ajoutable plus tard)") +
    (f.pihole.checked ? `\nPi-hole : ${nom}.${V?.config?.domaine_local || "lan"} vers ${f.ip.value}` : "") + (f.sauvegarde.checked ? "\nAjout à la tâche de sauvegarde" : "") +
    (archiveNouveau ? `\nPuis déploiement de ${archiveNouveau.name}` : "");
}
$("#form-nouveau").addEventListener("input", majRecap);
brancherDepot($("#depot-nouveau"), (fi) => { archiveNouveau = fi; majRecap(); });
let minuteurDns;
$("#form-nouveau").domaine.addEventListener("input", (e) => {
  clearTimeout(minuteurDns);
  minuteurDns = setTimeout(async () => {
    const d = e.target.value.trim();
    if (!d) { $("#aide-dns").textContent = "Caddy l'ajoute tout seul"; return; }
    try {
      const r = await api("/api/dns?d=" + encodeURIComponent(d));
      $("#aide-dns").textContent = r.pointe ? "Le DNS pointe déjà vers ta box" : r.attendu ? `DNS à créer : ${d} A ${r.attendu}` : "IP publique non réglée (PHARE_IP_PUBLIQUE)";
    } catch { $("#aide-dns").textContent = "domaine invalide"; }
  }, 600);
});
$("#form-nouveau").addEventListener("submit", (e) => {
  e.preventDefault();
  const f = e.target;
  const corps = { nom: f.nom.value.trim(), domaine: f.domaine.value.trim(), taille: f.taille.value, type: f.type.value,
    id: f.id.value.trim(), ip: f.ip.value.trim(), pihole: f.pihole.checked, sauvegarde: f.sauvegarde.checked };
  const archive = archiveNouveau;
  let info;
  suivreJob(`Création de « ${corps.nom} »`, async () => (info = await api("/api/creer", { methode: "POST", corps })), () => {
    nouveauPret = false; f.reset(); archiveNouveau = null;
    $("#depot-nouveau").classList.remove("plein");
    if (archive) suivreJob(`Déploiement de ${archive.name}`, () => api(`/api/deployer/${info.id}?type=${corps.type}`, { methode: "POST", brut: archive }));
  });
});

/* ------------------------------------------------------------------ JOURNAL */
async function rendreJournal() {
  try {
    const d = await api("/api/journal");
    $("#journal").innerHTML = d.journal.map((l) => `<li class="${l.ok ? "" : "ko"}">
      <time>${new Date(l.t * 1000).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" })}</time>
      <b>${esc(l.action)}</b><span>${esc(l.detail)}</span><small>${esc(l.client)}</small></li>`).join("") || `<li class="vide">Rien pour l'instant.</li>`;
  } catch (e) { $("#journal").innerHTML = `<li class="vide">${esc(e.message)}</li>`; }
}

/* ------------------------------------------------------------------ navigation */
function montrer(vue) {
  vueActive = vue;
  $$(".grille").forEach((g) => { g.classList.add("entree"); delete g.dataset.html; setTimeout(() => g.classList.remove("entree"), 1200); });
  $$(".rail nav button").forEach((b) => b.classList.toggle("actif", b.dataset.vue === vue));
  $$(".vue").forEach((s) => (s.hidden = s.id !== "vue-" + vue));
  $("#titre").innerHTML = TITRES[vue][0];
  $("#sur-titre").textContent = TITRES[vue][1];
  if (vue === "journal") rendreJournal();
  history.replaceState(null, "", "#" + vue);
  rendre();
}
$(".rail nav").addEventListener("click", (e) => { const v = e.target.closest("button")?.dataset.vue; if (v) montrer(v); });
$("#btn-actualiser").onclick = async () => {
  try { await api("/api/actualiser", { methode: "POST", corps: {} }); toast("Relecture lancée : Proxmox, Caddy, services, trafic"); setTimeout(charger, 2500); }
  catch (e) { toast(e.message, true); }
};

/* dégradé partagé des petites courbes */
document.body.insertAdjacentHTML("beforeend", `<svg width="0" height="0" style="position:absolute"><defs><linearGradient id="degrade-ambre" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#f2b544" stop-opacity=".35"/><stop offset="1" stop-color="#f2b544" stop-opacity="0"/></linearGradient></defs></svg>`);

montrer(TITRES[location.hash.slice(1)] ? location.hash.slice(1) : "sites");
addEventListener("hashchange", () => { const v = location.hash.slice(1); if (TITRES[v] && v !== vueActive) montrer(v); });
charger();
setInterval(() => { if (!document.hidden && $("#tiroir").hidden) charger(); }, 5000);
setInterval(() => { if (vueActive === "journal" && !document.hidden) rendreJournal(); }, 15000);
