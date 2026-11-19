"""Cœur de Phare : appels à l'agent, état en cache, actions, statistiques, journal.

Actions et lectures complètes par ``agent()`` (SSH vers l'hôte Proxmox, commande forcée ``phare-agent``) ;
état courant des CT par l'API Proxmox (jeton en lecture seule, ``lire_pve``).
Les lectures lentes tournent dans des fils de fond ; l'interface lit le cache (``vue()``).
"""

from __future__ import annotations

import datetime
import http.client
import json
import os
import random
import socket
import ssl
import struct
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import caddy

CLE_SSH = os.environ.get("PHARE_CLE", "/etc/phare/cle_agent")
HOTE_AGENT = os.environ.get("PHARE_AGENT", "root@192.168.10.2")
KNOWN_HOSTS = os.environ.get("PHARE_KNOWN_HOSTS", "/etc/phare/known_hosts")
DOSSIER = os.environ.get("PHARE_DONNEES", "/var/lib/phare")
IP_PUBLIQUE = os.environ.get("PHARE_IP_PUBLIQUE", "")  # IP publique de la box, pour vérifier le DNS
DEMO = os.environ.get("PHARE_DEMO") == "1"
CADDY_IP = os.environ.get("PHARE_CADDY", "192.168.10.4")
DNS_PUBLIC = os.environ.get("PHARE_DNS_PUBLIC", "1.1.1.1")
DOMAINE_EXEMPLE = os.environ.get("PHARE_DOMAINE_EXEMPLE", "exemple.fr")
# Réseau des CT, si l'agent ne le donne pas (il le lit dans /etc/phare/agent.conf).
RESEAU = os.environ.get("PHARE_RESEAU", "192.168.10")
BLOC_SITES = os.environ.get("PHARE_BLOC_SITES", "120-169")
BLOC_APPLIS = os.environ.get("PHARE_BLOC_APPLIS", "170-189")
# Lecture de l'état par l'API Proxmox, avec un jeton en lecture seule (rôle PVEAuditor).
# Sans jeton, Phare repasse par l'agent SSH.
PVE_API = os.environ.get("PHARE_PVE_API", "https://192.168.10.2:8006/api2/json")
PVE_JETON_ID = os.environ.get("PHARE_PVE_JETON_ID", "phare@pve!lecture")
PVE_JETON = os.environ.get("PHARE_PVE_JETON", "/etc/phare/pve-jeton")
PVE_CA = os.environ.get("PHARE_PVE_CA", "/etc/phare/pve-ca.pem")
# Cadences (secondes). « actif » : la page a été lue depuis moins de ACTIF_S secondes.
ACTIF_S = 180
PERIODES_ACTIF = {"complet": 300, "etat": 8, "caddy": 60, "services": 45, "sante": 300, "trafic": 300}
PERIODES_REPOS = {"complet": 900, "etat": 60, "caddy": 600, "services": 900, "sante": 300, "trafic": 600}


class ErreurPhare(Exception):
    """Erreur montrable à l'utilisateur."""


# --------------------------------------------------------------------------- agent

def agent(commande: str, entree: bytes | None = None, delai: int = 120, flux=None, taille: int | None = None) -> dict:
    """Appelle l'agent. ``flux`` : objet fichier lu par morceaux et envoyé sur stdin (archives)."""
    if DEMO:
        from . import demo
        return demo.agent(commande, entree)
    args = ["ssh", "-i", CLE_SSH, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={KNOWN_HOSTS}", HOTE_AGENT, commande]
    if flux is None:
        r = subprocess.run(args, input=entree or b"", capture_output=True, timeout=delai)
        sortie = r.stdout
    else:
        p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        reste = taille
        try:
            while reste is None or reste > 0:
                morceau = flux.read(min(1 << 20, reste) if reste is not None else 1 << 20)
                if not morceau:
                    break
                p.stdin.write(morceau)
                if reste is not None:
                    reste -= len(morceau)
        except BrokenPipeError:
            pass
        try:
            p.stdin.close()
        except (BrokenPipeError, ValueError):
            pass
        # stdin déjà fermé : communicate() voudrait le vider, on lit la réponse directement
        sortie = p.stdout.read()
        p.wait(timeout=delai)
    try:
        res = json.loads(sortie.decode("utf-8", "replace").strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise ErreurPhare("L'agent de l'hôte Proxmox ne répond pas.") from None
    if not res.get("ok"):
        raise ErreurPhare(res.get("erreur") or "Refus de l'agent.")
    return res


def api_pve_possible() -> bool:
    return not DEMO and os.path.isfile(PVE_JETON) and os.path.isfile(PVE_CA)


def lire_pve(chemin: str, delai: int = 8):
    """GET sur l'API Proxmox avec le jeton en lecture seule ; certificat vérifié par la CA du cluster."""
    with open(PVE_JETON, encoding="utf-8") as f:
        valeur = f.read().strip()
    ctx = ssl.create_default_context(cafile=PVE_CA)
    hote = PVE_API.split("/")[2]
    nom, _, port = hote.partition(":")
    c = http.client.HTTPSConnection(nom, int(port or 443), timeout=delai, context=ctx)
    try:
        c.request("GET", "/" + PVE_API.split("/", 3)[3] + chemin,
                  headers={"Authorization": f"PVEAPIToken={PVE_JETON_ID}={valeur}"})
        r = c.getresponse()
        corps = r.read()
        if r.status != 200:
            raise ErreurPhare(f"API Proxmox : HTTP {r.status}")
        return json.loads(corps)["data"]
    finally:
        c.close()


def fusionner_ressources(etat: dict, ressources: list[dict]) -> dict | None:
    """Met à jour les valeurs qui bougent (état, CPU, RAM, disque, uptime) d'un état complet de l'agent
    avec ``/cluster/resources``. Renvoie None si la liste des CT/VM a changé (relecture complète nécessaire)."""
    vms = {str(r.get("vmid")): r for r in ressources if r.get("type") in ("lxc", "qemu")}
    invites = etat.get("invites") or []
    if not invites or {i["id"] for i in invites} != set(vms):
        return None
    neufs = []
    for i in invites:
        r = vms[i["id"]]
        neufs.append({**i, "nom": r.get("name"), "etat": r.get("status"), "cpu": r.get("cpu"), "cpus": r.get("maxcpu"),
                      "mem": r.get("mem"), "maxmem": r.get("maxmem"), "disque": r.get("disk"),
                      "maxdisque": r.get("maxdisk"), "uptime": r.get("uptime"), "tags": r.get("tags", "")})
    noeud = dict(etat.get("noeud") or {})
    n = next((r for r in ressources if r.get("type") == "node"), None)
    if n:
        noeud.update({"cpu": n.get("cpu"), "cpus": n.get("maxcpu"), "mem": n.get("mem"),
                      "maxmem": n.get("maxmem"), "uptime": n.get("uptime")})
    return {**etat, "invites": neufs, "noeud": noeud}


# --------------------------------------------------------------------------- journal des actions

_verrou_journal = threading.Lock()


def noter(action: str, detail: str = "", ok: bool = True, client: str = "") -> None:
    ligne = {"t": time.time(), "action": action, "detail": detail, "ok": ok, "client": client}
    with _verrou_journal:
        os.makedirs(DOSSIER, exist_ok=True)
        chemin = os.path.join(DOSSIER, "journal.jsonl")
        with open(chemin, "a", encoding="utf-8") as f:
            f.write(json.dumps(ligne, ensure_ascii=False) + "\n")
        if os.path.getsize(chemin) > 2_000_000:
            with open(chemin, encoding="utf-8") as f:
                garde = f.readlines()[-3000:]
            with open(chemin, "w", encoding="utf-8") as f:
                f.writelines(garde)


def journal(n: int = 80) -> list[dict]:
    try:
        with open(os.path.join(DOSSIER, "journal.jsonl"), encoding="utf-8") as f:
            lignes = f.readlines()[-n:]
    except OSError:
        return []
    res = []
    for l in reversed(lignes):
        try:
            res.append(json.loads(l))
        except ValueError:
            pass
    return res


# --------------------------------------------------------------------------- statistiques

class Trafic:
    """Historique des visiteurs par domaine et par jour, gardé au-delà des 90 jours de GoAccess."""

    def __init__(self) -> None:
        self.chemin = os.path.join(DOSSIER, "trafic.json")
        self.verrou = threading.Lock()
        try:
            with open(self.chemin, encoding="utf-8") as f:
                self.donnees: dict[str, dict[str, list[int]]] = json.load(f)
        except (OSError, ValueError):
            self.donnees = {}
        self.maj = 0.0

    def fusionner(self, domaines: dict[str, list]) -> None:
        with self.verrou:
            for dom, jours in domaines.items():
                d = self.donnees.setdefault(dom, {})
                for jour in jours:
                    if isinstance(jour, list) and len(jour) >= 3 and isinstance(jour[0], str) and len(jour[0]) == 8:
                        d[jour[0]] = [int(jour[1] or 0), int(jour[2] or 0), int(jour[3] or 0) if len(jour) > 3 else 0]
            os.makedirs(DOSSIER, exist_ok=True)
            tmp = self.chemin + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.donnees, f)
            os.replace(tmp, self.chemin)
            self.maj = time.time()

    def resume(self, domaines: list[str]) -> dict:
        auj = datetime.date.today()
        jours = [(auj - datetime.timedelta(days=i)).strftime("%Y%m%d") for i in range(30)]
        par_jour = {j: [0, 0] for j in jours}
        total_v = total_h = 0
        premier = None
        with self.verrou:
            for dom in domaines:
                for jour, (v, h, *_r) in self.donnees.get(dom, {}).items():
                    total_v += v
                    total_h += h
                    if premier is None or jour < premier:
                        premier = jour
                    if jour in par_jour:
                        par_jour[jour][0] += v
                        par_jour[jour][1] += h
        serie = [par_jour[j][0] for j in reversed(jours)]
        return {
            "aujourdhui": par_jour[jours[0]][0],
            "j7": sum(par_jour[j][0] for j in jours[:7]),
            "j30": sum(par_jour[j][0] for j in jours),
            "total": total_v,
            "requetes_j7": sum(par_jour[j][1] for j in jours[:7]),
            "serie": serie,
            "depuis": premier,
        }


# --------------------------------------------------------------------------- DNS

def resoudre_a(domaine: str, serveur: str = DNS_PUBLIC) -> list[str]:
    """Requête DNS A minimale (UDP) vers un résolveur public : ce que voit Internet."""
    ident = random.randint(0, 65535)
    q = struct.pack(">HHHHHH", ident, 0x0100, 1, 0, 0, 0)
    for part in domaine.split("."):
        q += bytes([len(part)]) + part.encode()
    q += b"\x00" + struct.pack(">HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    try:
        s.sendto(q, (serveur, 53))
        rep, _ = s.recvfrom(1500)
    except OSError:
        return []
    finally:
        s.close()
    if len(rep) < 12 or struct.unpack(">H", rep[:2])[0] != ident:
        return []
    nq, nr = struct.unpack(">HH", rep[4:8])
    i = 12
    for _ in range(nq):
        while rep[i] != 0:
            if rep[i] & 0xC0 == 0xC0:
                i += 1
                break
            i += rep[i] + 1
        i += 5
    ips = []
    for _ in range(nr):
        if i >= len(rep):
            break
        if rep[i] & 0xC0 == 0xC0:
            i += 2
        else:
            while rep[i] != 0:
                i += rep[i] + 1
            i += 1
        typ, _cl, _ttl, lg = struct.unpack(">HHIH", rep[i:i + 10])
        i += 10
        if typ == 1 and lg == 4:
            ips.append(".".join(str(b) for b in rep[i:i + 4]))
        i += lg
    return ips


# --------------------------------------------------------------------------- état

def _rang_ip(i: dict) -> tuple[bool, bool, bool]:
    """Ordre de préférence entre invités de même adresse (voir ``Etat._ct_par_ip``)."""
    return (i.get("etat") == "running", bool(i.get("onboot")), not i.get("protege"))


def _bloc(texte, defaut: tuple[int, int]) -> tuple[int, int]:
    if isinstance(texte, (list, tuple)) and len(texte) == 2:
        return int(texte[0]), int(texte[1])
    try:
        a, b = str(texte).split("-", 1)
        return int(a), int(b)
    except (TypeError, ValueError):
        return defaut


def categorie(ip: str | None, ct: dict | None, reseau: dict | None = None) -> str:
    """« site », « appli » ou « infra ». Un CT tagué « site » est un site ; sinon on regarde le
    dernier octet de l'IP : bloc des sites, bloc des applis, le reste est de l'infra."""
    if ct and "site" in (ct.get("tags") or "").split(";"):
        return "site"
    reseau = reseau or {}
    prefixe = str(reseau.get("prefixe") or RESEAU) + "."
    sites = _bloc(reseau.get("sites") or BLOC_SITES, (120, 169))
    applis = _bloc(reseau.get("applis") or BLOC_APPLIS, (170, 189))
    if ip and ip.startswith(prefixe):
        try:
            n = int(ip.rsplit(".", 1)[1])
        except ValueError:
            return "infra"
        if sites[0] <= n <= sites[1]:
            return "site"
        if applis[0] <= n <= applis[1]:
            return "appli"
    return "infra"


class Etat:
    def __init__(self) -> None:
        self.verrou = threading.RLock()
        self.verrou_caddy = threading.Lock()
        self.etat: dict = {}
        self.caddy_texte = ""
        self.services: dict[str, dict] = {}
        self.sante: dict[str, dict] = {}
        self.trafic = Trafic()
        self.erreurs: dict[str, str] = {}
        self.maj: dict[str, float] = {}
        self.jobs: dict[str, dict] = {}  # jid -> {type, ct, domaine…}
        self.vu = 0.0  # dernière lecture de /api/vue
        self.pool = ThreadPoolExecutor(max_workers=8)
        try:
            with open(os.path.join(DOSSIER, "unites.json"), encoding="utf-8") as f:
                self.memo_unites: dict[str, str] = json.load(f)
        except (OSError, ValueError):
            self.memo_unites = {}

    # ----- rafraîchissements
    def _essayer(self, nom: str, fn) -> None:
        try:
            fn()
            self.erreurs.pop(nom, None)
            self.maj[nom] = time.time()
        except Exception as e:  # noqa: BLE001
            self.erreurs[nom] = str(e)[:200]

    def actif(self) -> bool:
        """Quelqu'un regarde la page (``/api/vue`` lu depuis moins de ACTIF_S secondes)."""
        return time.time() - self.vu < ACTIF_S

    def rafraichir_etat(self, complet: bool = False) -> None:
        """État des CT/VM. Par l'API Proxmox (un seul appel) quand c'est possible ; par l'agent SSH
        pour la lecture complète (IP, presets, sauvegarde) : au démarrage, après une action,
        quand la liste des CT change, et à la cadence « complet »."""
        if not complet and api_pve_possible() and self.etat.get("invites"):
            try:
                neuf = fusionner_ressources(self.etat, lire_pve("/cluster/resources"))
                if neuf is not None:
                    with self.verrou:
                        self.etat = neuf
                    self.erreurs.pop("api", None)
                    self.erreurs.pop("etat", None)
                    self.maj["etat"] = time.time()
                    return
            except Exception as e:  # noqa: BLE001
                self.erreurs["api"] = ("API Proxmox : " + str(e))[:200]
        def f():
            self.etat = agent("etat", delai=40)
            self.maj["complet"] = time.time()
        self._essayer("etat", f)

    def rafraichir_caddy(self) -> None:
        def f():
            self.caddy_texte = agent("caddy-lire", delai=30)["texte"]
        self._essayer("caddy", f)

    def rafraichir_trafic(self) -> None:
        self._essayer("trafic", lambda: self.trafic.fusionner(agent("trafic", delai=60)["domaines"]))

    def rafraichir_services(self, ids: list[str] | None = None) -> None:
        if ids is None:
            ids = [g["ct"]["id"] for g in self._groupes() if g["ct"] and g["ct"]["type"] == "lxc"]
            ids += [c["id"] for c in self._cts_sites_sans_domaine()]
        ids = sorted(set(ids))
        if len(ids) > 1:
            # une seule connexion SSH pour tous les CT
            try:
                cts = agent("services-tous " + " ".join(ids), delai=180)["cts"]
                t = time.time()
                for ctid in ids:
                    v = cts.get(ctid) or {"erreur": "absent de la réponse de l'agent", "ecoute": [], "unites": {}}
                    self.services[ctid] = {**v, "t": t}
                return
            except Exception:  # noqa: BLE001  (agent ancien ou erreur : un appel par CT, comme avant)
                pass
        def un(ctid):
            try:
                self.services[ctid] = {**agent(f"services {ctid}", delai=30), "t": time.time()}
            except Exception as e:  # noqa: BLE001
                self.services[ctid] = {"erreur": str(e)[:200], "ecoute": [], "unites": {}, "t": time.time()}
        list(self.pool.map(un, ids))

    def verifier_sante(self) -> None:
        """Interroge chaque site comme un visiteur : HTTPS vers Caddy (PHARE_CADDY) avec le vrai nom
        (SNI + Host), certificat vérifié. Teste toute la chaîne, y compris les CT dont le pare-feu
        n'accepte que Caddy. Le trafic venant du LAN est exclu des statistiques."""
        cibles = {}
        for g in self._groupes():
            publics = [d["nom"] for d in g["domaines"] if not d["lan"] and not d["maintenance"]]
            if publics and g["ip"]:
                cibles[f"{g['ip']}:{g['port']}"] = publics[0]
        if DEMO:  # en démo, aucune connexion réseau : réponses inventées
            for cle, dom in cibles.items():
                self.sante[cle] = {"ok": True, "code": 200, "ms": random.randint(18, 140), "t": time.time(), "via": dom}
            return
        ctx = ssl.create_default_context()
        def un(item):
            cle, dom = item
            t0 = time.time()
            try:
                c = http.client.HTTPSConnection(CADDY_IP, 443, timeout=6, context=ctx)
                c.sock = ctx.wrap_socket(socket.create_connection((CADDY_IP, 443), timeout=6), server_hostname=dom)
                c.request("GET", "/", headers={"User-Agent": "Phare/1.0 (sante)", "Host": dom})
                code = c.getresponse().status
                c.close()
                self.sante[cle] = {"ok": code < 500, "code": code, "ms": round((time.time() - t0) * 1000), "t": time.time(), "via": dom}
            except ssl.SSLError as e:
                self.sante[cle] = {"ok": False, "code": None, "erreur": "certificat", "detail": str(e)[:120], "t": time.time(), "via": dom}
            except OSError as e:
                self.sante[cle] = {"ok": False, "code": None, "erreur": "injoignable", "detail": e.__class__.__name__, "t": time.time(), "via": dom}
        list(self.pool.map(un, cibles.items()))

    def boucle(self) -> None:
        """Fil de fond, cadences PERIODES_ACTIF quand la page est ouverte, PERIODES_REPOS sinon.
        Ouverte : état (API) 8 s, Caddy 60 s, services 45 s. Fermée : état 60 s, Caddy 10 min, services 15 min.
        Santé toutes les 5 min. À la réouverture, Caddy et services sont relus aussitôt."""
        dernier = {k: 0.0 for k in PERIODES_ACTIF}
        actions = {"complet": lambda: self.rafraichir_etat(complet=True), "etat": self.rafraichir_etat,
                   "caddy": self.rafraichir_caddy, "services": self.rafraichir_services,
                   "sante": self.verifier_sante, "trafic": self.rafraichir_trafic}
        actif_avant = False
        while True:
            actif = self.actif()
            if actif and not actif_avant:
                dernier["caddy"] = dernier["services"] = 0.0
            actif_avant = actif
            periodes = PERIODES_ACTIF if actif else PERIODES_REPOS
            for nom, periode in periodes.items():
                now = time.time()
                if now - dernier[nom] >= periode:
                    dernier[nom] = now
                    if nom == "complet":
                        dernier["etat"] = now
                    actions[nom]()
            time.sleep(4)

    # ----- vue
    def _invites(self) -> dict[str, dict]:
        return {i["id"]: i for i in self.etat.get("invites", [])}

    def _ct_par_ip(self) -> dict[str, dict]:
        """Invité retenu pour chaque adresse. Deux invités peuvent porter la même adresse, par
        exemple un ancien CT gardé quelques jours, arrêté, à côté de son remplaçant. Le CT en
        marche l'emporte, puis celui qui démarre seul (onboot), puis celui qui n'est pas
        protégé ; à égalité, le premier lu."""
        par_ip: dict[str, dict] = {}
        for i in self.etat.get("invites", []):
            ip = i.get("ip")
            if ip and (ip not in par_ip or _rang_ip(i) > _rang_ip(par_ip[ip])):
                par_ip[ip] = i
        return par_ip

    def _groupes(self) -> list[dict]:
        """Sites Caddy regroupés par amont (ip:port)."""
        par_ip = self._ct_par_ip()
        groupes: dict[str, dict] = {}
        for s in caddy.sites(self.caddy_texte):
            noms = [a for a in s["adresses"] if "." in a and not a.startswith(":")]
            if not noms:
                continue
            ip, port = s["amont_ip"], s["amont_port"]
            cle = f"{ip}:{port}" if ip else "sans-amont:" + noms[0]
            g = groupes.get(cle)
            if g is None:
                ct = par_ip.get(ip) if ip else None
                g = groupes[cle] = {
                    "cle": cle, "ip": ip, "port": port, "ct": ct,
                    "https": (s["amont_brut"] or "").lower().startswith("https://") if s["amont_brut"] else None,
                    "domaines": [], "categorie": categorie(ip, ct, self.etat.get("reseau")),
                }
            for n in noms:
                g["domaines"].append({"nom": n, "maintenance": s["maintenance"], "trafic": s["trafic"],
                                      "phare": s["phare"], "lan": n.endswith(".lan")})
        return list(groupes.values())

    def _cts_sites_sans_domaine(self) -> list[dict]:
        pris = {g["ct"]["id"] for g in self._groupes() if g["ct"]}
        retenus = self._ct_par_ip()
        # un invité dont l'adresse est retenue pour un autre (ancien CT gardé à côté de son remplaçant) n'est pas « libre »
        return [i for i in self.etat.get("invites", [])
                if i.get("type") == "lxc" and i["id"] not in pris and categorie(i.get("ip"), i, self.etat.get("reseau")) == "site"
                and not (i.get("ip") and retenus.get(i["ip"], i)["id"] != i["id"])]

    def unite_du_groupe(self, g: dict) -> str | None:
        """Service systemd qui écoute sur le port du site. Retenu sur disque : quand le site est
        éteint, le service n'écoute plus mais Phare sait encore lequel relancer."""
        if not g["ct"]:
            return None
        cle = f"{g['ct']['id']}:{g['port']}"
        sv = self.services.get(g["ct"]["id"], {})
        for e in sv.get("ecoute", []):
            if e["port"] == g["port"] and e.get("unite"):
                if self.memo_unites.get(cle) != e["unite"]:
                    self.memo_unites[cle] = e["unite"]
                    self._sauver_memo()
                return e["unite"]
        return self.memo_unites.get(cle)

    def _sauver_memo(self) -> None:
        try:
            os.makedirs(DOSSIER, exist_ok=True)
            with open(os.path.join(DOSSIER, "unites.json"), "w", encoding="utf-8") as f:
                json.dump(self.memo_unites, f)
        except OSError:
            pass

    def vue(self) -> dict:
        with self.verrou:
            groupes = self._groupes()
            res = []
            for g in groupes:
                ct = g["ct"]
                publics = [d["nom"] for d in g["domaines"] if not d["lan"]]
                unite = self.unite_du_groupe(g)
                sv = self.services.get(ct["id"], {}) if ct else {}
                etat_unite = sv.get("unites", {}).get(unite) if unite else None
                if unite and etat_unite is None and ct and ct.get("etat") == "running" and "ecoute" in sv:
                    etat_unite = "arrêté"
                maint = [d["maintenance"] for d in g["domaines"] if not d["lan"]] or [d["maintenance"] for d in g["domaines"]]
                res.append({
                    **g,
                    "nom": publics[0] if publics else g["domaines"][0]["nom"],
                    "unite": unite,
                    "unite_etat": etat_unite,
                    "ecoute": sv.get("ecoute", []),
                    "maintenance": all(maint) if maint else False,
                    "maintenance_partielle": any(maint) and not all(maint),
                    "sante": self.sante.get(f"{g['ip']}:{g['port']}"),
                    "stats": self.trafic.resume([d["nom"] for d in g["domaines"]]),
                })
            ordre = {"site": 0, "appli": 1, "infra": 2}
            res.sort(key=lambda x: (ordre[x["categorie"]], -(x["stats"]["j7"]), x["nom"]))
            return {
                "groupes": res,
                "cts_libres": self._cts_sites_sans_domaine(),
                "invites": self.etat.get("invites", []),
                "noeud": self.etat.get("noeud", {}),
                "prochain_id": self.etat.get("prochain_id"),
                "prochaine_ip": self.etat.get("prochaine_ip"),
                "presets": self.etat.get("presets", {}),
                "sauvegarde": self.etat.get("sauvegarde", []),
                "services": {k: {"ecoute": v.get("ecoute", []), "unites": v.get("unites", {})} for k, v in self.services.items()},
                "erreurs": self.erreurs,
                "maj": self.maj,
                "ip_publique": IP_PUBLIQUE,
                "config": {
                    **(self.etat.get("hote") or {}),
                    "domaine_exemple": DOMAINE_EXEMPLE,
                },
                "jobs": self.jobs,
                "demo": DEMO,
                "t": time.time(),
            }

    def groupe(self, cle: str) -> dict:
        for g in self._groupes():
            if g["cle"] == cle:
                return g
        raise ErreurPhare("Site inconnu (la page n'était peut-être plus à jour).")

    # ----- Caddy
    def modifier_caddy(self, fn, action: str, detail: str, client: str) -> dict:
        """Relit le Caddyfile, applique ``fn(texte)``, l'écrit (l'agent valide et sauvegarde)."""
        with self.verrou_caddy:
            texte = agent("caddy-lire", delai=30)["texte"]
            try:
                nouveau = fn(texte)
            except caddy.ErreurCaddy as e:
                raise ErreurPhare(str(e)) from None
            if nouveau == texte:
                return {"inchange": True}
            try:
                res = agent("caddy-ecrire", entree=nouveau.encode("utf-8"), delai=150)
            except ErreurPhare as e:
                noter(action, f"{detail} : {e}", ok=False, client=client)
                raise
            self.caddy_texte = nouveau
            noter(action, f"{detail} (sauvegarde {res.get('sauvegarde')})", client=client)
            return res


ETAT = Etat()
