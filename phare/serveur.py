"""Serveur HTTP de Phare (bibliothèque standard seulement).

Phare n'a pas de mot de passe. Protections :
- le pare-feu du CT n'ouvre le port 80 qu'au réseau local et au VPN ;
- en-tête ``Host`` vérifié (contre le DNS rebinding) ;
- toute requête qui modifie quelque chose exige l'en-tête ``X-Phare: 1`` : un site tiers
  ne peut pas l'envoyer sans pré-vol CORS, que Phare ne valide jamais.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import caddy
from .moteur import ETAT, ErreurPhare, agent, journal, noter, resoudre_a, IP_PUBLIQUE

WEB = os.environ.get("PHARE_WEB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))
HOTES = set(filter(None, os.environ.get("PHARE_HOTES", "phare.lan,phare,localhost,127.0.0.1").split(",")))
MAX_ARCHIVE = 300 * 1024 * 1024
PORTS_DEFAUT = {"statique": 80, "node": 3000, "next": 3000}
NOM_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,40}[a-z0-9])?$")
ID_RE = re.compile(r"^\d{3,9}$")


def _int(v, nom="valeur") -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ErreurPhare(f"{nom} invalide.") from None


def verifier_confirmation(d: dict, attendu: str) -> None:
    """Action lourde (restaurer un instantané, retirer ou rediriger un domaine) : le nom retapé
    doit être exactement le nom affiché. Contrôle côté serveur, en plus de celui de la page."""
    saisie = str(d.get("confirmation", "")).strip()
    if not attendu or saisie != attendu:
        raise ErreurPhare(f"Confirmation refusée : retape exactement « {attendu} ».")


def nom_du_groupe(g: dict) -> str:
    """Nom affiché d'un site (même règle que Etat.vue) : premier domaine public, sinon le premier."""
    publics = [x["nom"] for x in g["domaines"] if not x.get("lan")]
    return publics[0] if publics else g["domaines"][0]["nom"]


class Gestionnaire(BaseHTTPRequestHandler):
    server_version = "Phare/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # journal systemd plus calme
        if not self.path.startswith(("/api/vue", "/api/job")):
            super().log_message(fmt, *args)

    # ----- réponses
    def _json(self, obj, code=200):
        corps = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corps)

    def _erreur(self, message, code=400):
        self._json({"ok": False, "erreur": message}, code)

    def _hote_ok(self) -> bool:
        hote = (self.headers.get("Host") or "").split(":")[0].lower()
        return hote in HOTES

    def _corps_json(self) -> dict:
        n = _int(self.headers.get("Content-Length") or 0, "Taille")
        if n > 256 * 1024:
            raise ErreurPhare("Requête trop grosse.")
        try:
            d = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise ErreurPhare("JSON illisible.") from None
        if not isinstance(d, dict):
            raise ErreurPhare("Objet JSON attendu.")
        return d

    @property
    def client(self) -> str:
        return self.client_address[0]

    # ----- GET
    def do_GET(self):
        if not self._hote_ok():
            return self._erreur("Hôte refusé.", 421)
        u = urlparse(self.path)
        p = u.path
        try:
            if p == "/api/vue":
                ETAT.vu = time.time()
                return self._json(ETAT.vue())
            if p == "/api/journal":
                return self._json({"journal": journal(120)})
            if p == "/api/abysse/sites":
                return self._json(caddy.sites_pour_abysse(ETAT.caddy_texte))
            if p == "/api/caddy/sauvegardes":
                return self._json(agent("caddy-sauvegardes"))
            if m := re.fullmatch(r"/api/ct/(\d+)/snapshots", p):
                return self._json(agent(f"snapshots {m.group(1)}"))
            if m := re.fullmatch(r"/api/job/([0-9a-f]{12})", p):
                res = agent(f"job {m.group(1)}", delai=20)
                meta = ETAT.jobs.get(m.group(1), {})
                if res.get("etat") != "en cours" and not meta.get("termine"):
                    meta["termine"] = True
                    noter(meta.get("action", "tâche"), f"{meta.get('detail', '')} : {res.get('etat')}",
                          ok=res.get("etat") == "fini", client=meta.get("client", ""))
                    threading.Thread(target=ETAT.rafraichir_etat, kwargs={"complet": True}, daemon=True).start()
                return self._json({**res, "meta": meta})
            if p == "/api/dns":
                dom = caddy.valider_domaine(parse_qs(u.query).get("d", [""])[0])
                ips = resoudre_a(dom)
                return self._json({"domaine": dom, "ips": ips, "attendu": IP_PUBLIQUE or None, "pointe": bool(IP_PUBLIQUE) and IP_PUBLIQUE in ips})
            return self._statique(p)
        except (ErreurPhare, caddy.ErreurCaddy) as e:
            return self._erreur(str(e))

    def _statique(self, p: str):
        if p == "/":
            p = "/index.html"
        chemin = os.path.realpath(os.path.join(WEB, p.lstrip("/")))
        if not chemin.startswith(os.path.realpath(WEB) + os.sep) or not os.path.isfile(chemin):
            return self._erreur("Introuvable.", 404)
        with open(chemin, "rb") as f:
            corps = f.read()
        typ = mimetypes.guess_type(chemin)[0] or "application/octet-stream"
        if typ.startswith("text/") or typ in ("application/javascript",):
            typ += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(corps)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(corps)

    # ----- POST
    def do_POST(self):
        if not self._hote_ok():
            return self._erreur("Hôte refusé.", 421)
        if self.headers.get("X-Phare") != "1":
            return self._erreur("En-tête X-Phare manquant.", 403)
        u = urlparse(self.path)
        p = u.path
        try:
            if m := re.fullmatch(r"/api/deployer/(\d+)", p):
                return self._json(self._deployer(m.group(1), parse_qs(u.query).get("type", [""])[0]))
            d = self._corps_json()
            if m := re.fullmatch(r"/api/ct/(\d+)/(start|shutdown|reboot)", p):
                ctid, action = m.groups()
                res = agent(f"ct {ctid} {action}", delai=150)
                noter({"start": "CT allumé", "shutdown": "CT éteint", "reboot": "CT redémarré"}[action], f"CT {ctid}", client=self.client)
                ETAT.rafraichir_etat(complet=True)
                threading.Thread(target=ETAT.rafraichir_services, args=([ctid],), daemon=True).start()
                return self._json(res)
            if m := re.fullmatch(r"/api/ct/(\d+)/snapshot", p):
                res = agent(f"snapshot {m.group(1)} manuel depuis Phare", delai=150)
                noter("Snapshot", f"CT {m.group(1)} : {res['snapshot']}", client=self.client)
                return self._json(res)
            if m := re.fullmatch(r"/api/ct/(\d+)/restaurer", p):
                nom = str(d.get("nom", ""))
                inv = ETAT._invites().get(m.group(1))
                if not inv:
                    raise ErreurPhare("CT inconnu (la page n'était peut-être plus à jour).")
                verifier_confirmation(d, str(inv.get("nom", "")))
                res = agent(f"restaurer-snapshot {m.group(1)} {nom}", delai=400)
                noter("Snapshot restauré", f"CT {m.group(1)} revenu à {nom}", client=self.client)
                return self._json(res)
            if m := re.fullmatch(r"/api/site/(allumer|eteindre|redemarrer)", p):
                return self._json(self._site(m.group(1), str(d.get("cle", ""))))
            if p == "/api/caddy/ajouter":
                dom = caddy.valider_domaine(str(d.get("domaine", "")))
                ip, port = str(d.get("ip", "")), _int(d.get("port"), "Port")
                res = ETAT.modifier_caddy(lambda t: caddy.ajouter_site(t, dom, ip, port, f"Ajouté par Phare le {time.strftime('%d/%m/%Y')}"),
                                          "Domaine ajouté", f"{dom} vers {ip}:{port}", self.client)
                return self._json(res)
            if p == "/api/caddy/amont":
                ip, port = str(d.get("ip", "")), _int(d.get("port"), "Port")
                g = ETAT.groupe(str(d.get("cle", "")))
                verifier_confirmation(d, nom_du_groupe(g))
                noms = [x["nom"] for x in g["domaines"]]
                def fn(t):
                    for n in noms:
                        t = caddy.changer_amont(t, n, ip, port)
                    return t
                res = ETAT.modifier_caddy(fn, "Destination changée", f"{', '.join(noms)} : {g['cle']} remplacé par {ip}:{port}", self.client)
                return self._json(res)
            if p == "/api/caddy/retirer":
                dom = str(d.get("domaine", ""))
                verifier_confirmation(d, dom)
                return self._json(ETAT.modifier_caddy(lambda t: caddy.retirer_site(t, dom), "Domaine retiré", dom, self.client))
            if p == "/api/caddy/maintenance":
                dom, active = str(d.get("domaine", "")), bool(d.get("active"))
                return self._json(ETAT.modifier_caddy(lambda t: caddy.maintenance(t, dom, active),
                                                      "Maintenance " + ("activée" if active else "retirée"), dom, self.client))
            if p == "/api/caddy/restaurer":
                nom = str(d.get("nom", ""))
                texte = agent(f"caddy-sauvegarde {nom}")["texte"]
                return self._json(ETAT.modifier_caddy(lambda _t: texte, "Caddyfile restauré", nom, self.client))
            if p == "/api/creer":
                return self._json(self._creer(d))
            if p == "/api/actualiser":
                def tout():
                    ETAT.rafraichir_etat(complet=True); ETAT.rafraichir_caddy(); ETAT.rafraichir_services()
                    ETAT.verifier_sante(); ETAT.rafraichir_trafic()
                threading.Thread(target=tout, daemon=True).start()
                return self._json({"ok": True})
            return self._erreur("Introuvable.", 404)
        except (ErreurPhare, caddy.ErreurCaddy) as e:
            return self._erreur(str(e))

    # ----- actions composées
    def _site(self, action: str, cle: str) -> dict:
        g = ETAT.groupe(cle)
        ct = g["ct"]
        noms = [x["nom"] for x in g["domaines"]]
        unite = ETAT.unite_du_groupe(g)
        if action == "eteindre":
            if ct and unite and ct.get("etat") == "running":
                agent(f"service {ct['id']} stop {unite}", delai=90)
            def fn(t):
                for n in noms:
                    t = caddy.maintenance(t, n, True)
                return t
            ETAT.modifier_caddy(fn, "Site éteint", f"{noms[0]} (service {unite or '?'} arrêté, page de maintenance)", self.client)
        else:
            if ct and ct.get("etat") != "running":
                agent(f"ct {ct['id']} start", delai=120)
                time.sleep(3)
            if ct and unite:
                agent(f"service {ct['id']} {'restart' if action == 'redemarrer' else 'start'} {unite}", delai=90)
            def fn(t):
                for n in noms:
                    t = caddy.maintenance(t, n, False)
                return t
            if action == "allumer":
                ETAT.modifier_caddy(fn, "Site allumé", f"{noms[0]} (service {unite or '?'})", self.client)
            else:
                noter("Site redémarré", f"{noms[0]} (service {unite or '?'})", client=self.client)
        ETAT.rafraichir_etat(complet=True)
        if ct:
            ETAT.rafraichir_services([ct["id"]])
        ETAT.verifier_sante()
        return {"ok": True}

    def _creer(self, d: dict) -> dict:
        nom = str(d.get("nom", "")).strip().lower()
        if not NOM_RE.match(nom):
            raise ErreurPhare("Nom du CT : minuscules, chiffres et tirets (ex. mon-site).")
        typ = str(d.get("type", ""))
        if typ not in PORTS_DEFAUT:
            raise ErreurPhare("Type de site inconnu.")
        ctid = str(d.get("id") or ETAT.etat.get("prochain_id") or "")
        ip = str(d.get("ip") or ETAT.etat.get("prochaine_ip") or "")
        domaine = str(d.get("domaine", "")).strip()
        if domaine:
            domaine = caddy.valider_domaine(domaine)
            if any(domaine in [x["nom"] for x in g["domaines"]] for g in ETAT._groupes()):
                raise ErreurPhare(f"{domaine} est déjà dans Caddy.")
        demande = {"id": ctid, "nom": nom, "ip": ip, "taille": str(d.get("taille", "")), "type": typ,
                   "pihole": bool(d.get("pihole", True)), "sauvegarde": bool(d.get("sauvegarde", True))}
        res = agent("creer", entree=json.dumps(demande).encode(), delai=60)
        detail = f"CT {ctid} « {nom} » {demande['taille']} {typ} en {ip}"
        ETAT.jobs[res["job"]] = {"action": "CT créé", "detail": detail, "ct": ctid, "client": self.client, "t": time.time()}
        noter("Création lancée", detail, client=self.client)
        if domaine:
            port = PORTS_DEFAUT[typ]
            ETAT.modifier_caddy(lambda t: caddy.ajouter_site(t, domaine, ip, port, f"CT {ctid} {nom}, créé par Phare le {time.strftime('%d/%m/%Y')}"),
                                "Domaine ajouté", f"{domaine} vers {ip}:{port}", self.client)
        return {"job": res["job"], "id": ctid, "ip": ip, "domaine": domaine}

    def _deployer(self, ctid: str, typ: str) -> dict:
        if typ not in PORTS_DEFAUT:
            raise ErreurPhare("Type de site inconnu.")
        n = _int(self.headers.get("Content-Length") or 0, "Taille")
        if not 0 < n <= MAX_ARCHIVE:
            raise ErreurPhare("Archive vide ou trop grosse (300 Mo au plus).")
        res = agent(f"deployer {ctid} {typ}", flux=self.rfile, taille=n, delai=600)
        detail = f"CT {ctid} ({typ}, {n // 1024} Ko)"
        ETAT.jobs[res["job"]] = {"action": "Déploiement", "detail": detail, "ct": ctid, "client": self.client, "t": time.time()}
        noter("Déploiement lancé", detail, client=self.client)
        return res


def lancer(hote: str = "0.0.0.0", port: int = 80) -> None:
    threading.Thread(target=ETAT.boucle, daemon=True).start()
    # page de maintenance poussée une fois au démarrage
    def pousser_page():
        try:
            with open(os.path.join(WEB, "maintenance.html"), "rb") as f:
                agent("page-maintenance", entree=f.read(), delai=40)
        except (OSError, ErreurPhare) as e:
            ETAT.erreurs["page-maintenance"] = str(e)[:200]
    threading.Thread(target=pousser_page, daemon=True).start()
    serveur = ThreadingHTTPServer((hote, port), Gestionnaire)
    serveur.daemon_threads = True
    print(f"Phare écoute sur http://{hote}:{port}", flush=True)
    serveur.serve_forever()
