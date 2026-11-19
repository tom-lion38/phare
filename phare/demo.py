"""Mode démo (PHARE_DEMO=1) : un faux agent en mémoire, pour essayer l'interface sans rien toucher.

Toutes les données sont inventées : réseau 192.168.10.0/24, domaines en exemple.fr.
"""

from __future__ import annotations

import datetime
import random
import time
import uuid

RESEAU = "192.168.10"

# domaine, ip, port, CT, nom du CT, service, cœurs, mémoire (Mo)
_SITES = [
    ("exemple.fr", f"{RESEAU}.120", 80, "105", "vitrine", "nginx.service", 1, 512),
    ("blog.exemple.fr", f"{RESEAU}.121", 80, "106", "blog", "nginx.service", 1, 512),
    ("boutique.exemple.fr", f"{RESEAU}.122", 3000, "107", "boutique", "site.service", 2, 2048),
    ("api.exemple.fr", f"{RESEAU}.123", 3000, "108", "api", "site.service", 2, 1024),
    ("notes.exemple.fr", f"{RESEAU}.170", 3000, "109", "notes", "site.service", 1, 1024),
    ("grafana.exemple.fr", f"{RESEAU}.17", 3000, "104", "supervision", "grafana-server.service", 2, 2048),
]

# CT de l'infrastructure (protégés) et un CT de site sans domaine
_AUTRES = [
    {"id": "100", "nom": "pihole", "ip": f"{RESEAU}.3", "cpus": 1, "maxmem": 512 << 20, "protege": True, "etat": "running"},
    {"id": "101", "nom": "caddy", "ip": f"{RESEAU}.4", "cpus": 1, "maxmem": 512 << 20, "protege": True, "etat": "running"},
    {"id": "102", "nom": "trafic", "ip": f"{RESEAU}.6", "cpus": 1, "maxmem": 512 << 20, "protege": True, "etat": "running"},
    {"id": "103", "nom": "phare", "ip": f"{RESEAU}.5", "cpus": 1, "maxmem": 512 << 20, "protege": True, "etat": "running"},
    {"id": "110", "nom": "essai", "ip": f"{RESEAU}.125", "cpus": 1, "maxmem": 512 << 20, "protege": False, "etat": "stopped", "tags": "site"},
]


def _caddyfile() -> str:
    lignes = ["(trafic) {", "\tlog trafic {", "\t\tformat json", "\t}", "}", ""]
    for dom, ip, port, *_ in _SITES:
        lignes += [f"{dom} {{", "\timport trafic", f"\treverse_proxy {ip}:{port}", "}", ""]
    lignes += ["www.exemple.fr {", "\timport trafic", "\tredir https://exemple.fr{uri} permanent", "}", "",
               "vitrine.lan {", f"\treverse_proxy {RESEAU}.120:80", "}", ""]
    return "\n".join(lignes)


class _Demo:
    def __init__(self):
        self.caddy = _caddyfile()
        self.etats = {s[3]: "running" for s in _SITES}
        self.etats.update({a["id"]: a["etat"] for a in _AUTRES})
        self.unites = {s[3]: "running" for s in _SITES}
        self.jobs: dict[str, dict] = {}
        self.sauvegardes: list[tuple[str, str]] = []
        rnd = random.Random(4)
        auj = datetime.date.today()
        self.trafic = {}
        for i, s in enumerate(_SITES):
            base = rnd.choice([3, 8, 15, 40, 90])
            self.trafic[s[0]] = [[(auj - datetime.timedelta(days=d)).strftime("%Y%m%d"),
                                  max(0, int(base * (0.6 + rnd.random()) * (0.5 if d == 0 else 1))), base * 12, base * 50000]
                                 for d in range(60 + i * 3)]

    def invites(self):
        res = []
        for _dom, ip, _port, ctid, nom, _unite, cores, mem in _SITES:
            on = self.etats[ctid] == "running"
            dernier = int(ip.split(".")[-1])
            res.append({"id": ctid, "type": "lxc", "nom": nom, "etat": self.etats[ctid], "cpu": random.random() * 0.08 if on else 0,
                        "cpus": cores, "mem": mem * 1048576 * random.uniform(.2, .6) if on else 0, "maxmem": mem * 1048576,
                        "disque": 2e9, "maxdisque": 8e9, "uptime": 86400 * 3 if on else 0,
                        "tags": "site" if 120 <= dernier <= 169 else "", "ip": ip, "onboot": True,
                        "protege": ctid == "104"})
        for a in _AUTRES:
            on = self.etats[a["id"]] == "running"
            res.append({"id": a["id"], "type": "lxc", "nom": a["nom"], "etat": self.etats[a["id"]],
                        "cpu": random.random() * 0.03 if on else 0, "cpus": a["cpus"],
                        "mem": a["maxmem"] * random.uniform(.2, .5) if on else 0, "maxmem": a["maxmem"],
                        "disque": 1e9, "maxdisque": 4e9, "uptime": 86400 * 9 if on else 0, "tags": a.get("tags", ""),
                        "ip": a["ip"], "onboot": True, "protege": a["protege"]})
        res.append({"id": "200", "type": "qemu", "nom": "nas", "etat": "running", "cpu": .04, "cpus": 4, "mem": 6e9,
                    "maxmem": 16 << 30, "disque": 0, "maxdisque": 32e9, "uptime": 86400 * 9, "tags": "", "protege": False})
        return sorted(res, key=lambda x: int(x["id"]))

    def agent(self, commande: str, entree: bytes | None):
        v, *a = commande.split()
        time.sleep(0.15)
        if v == "etat":
            return {"ok": True, "invites": self.invites(),
                    "noeud": {"cpu": .11 + random.random() * .05, "cpus": 8, "mem": 21e9, "maxmem": 64e9, "uptime": 9e5, "charge": ["1.2", "1.1", "0.9"]},
                    "prochain_id": "111", "prochaine_ip": f"{RESEAU}.126", "sauvegarde": ["104", "105", "106", "107", "108"],
                    "presets": {"petit": {"cores": 1, "memory": 512, "swap": 256, "disk": 4},
                                "moyen": {"cores": 2, "memory": 1024, "swap": 512, "disk": 8},
                                "grand": {"cores": 4, "memory": 4096, "swap": 1024, "disk": 16}},
                    "reseau": {"prefixe": RESEAU, "sites": [120, 169], "applis": [170, 189]},
                    "hote": {"noeud": "pve", "ct_caddy": "101", "domaine_local": "lan",
                             "pihole": True, "trafic": True, "sauvegarde": True}}
        if v == "caddy-lire":
            return {"ok": True, "texte": self.caddy}
        if v == "caddy-ecrire":
            horo = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            self.sauvegardes.insert(0, (f"Caddyfile.{horo}", self.caddy))
            self.caddy = entree.decode()
            return {"ok": True, "sauvegarde": f"Caddyfile.{horo}"}
        if v == "caddy-sauvegardes":
            return {"ok": True, "sauvegardes": [n for n, _ in self.sauvegardes]}
        if v == "caddy-sauvegarde":
            return {"ok": True, "texte": dict(self.sauvegardes).get(a[0], self.caddy)}
        if v == "trafic":
            return {"ok": True, "domaines": self.trafic}
        if v in ("services", "services-tous"):
            ids = a if v == "services-tous" else a[:1]
            res = {i: self._services(i) for i in ids}
            return {"ok": True, "cts": res} if v == "services-tous" else {"ok": True, **res[ids[0]]}
        if v == "ct":
            self.etats[a[0]] = "running" if a[1] in ("start", "reboot") else "stopped"
            return {"ok": True, "etat": self.etats[a[0]]}
        if v == "service":
            self.unites[a[0]] = "running" if a[1] in ("start", "restart") else "dead"
            return {"ok": True, "unite": a[2], "etat": self.unites[a[0]]}
        if v == "snapshot":
            return {"ok": True, "snapshot": "phare_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}
        if v == "snapshots":
            hier = datetime.datetime.now() - datetime.timedelta(days=3)
            return {"ok": True, "snapshots": [{"nom": "phare_" + hier.strftime("%Y%m%d_%H%M%S"), "date": hier.timestamp(),
                                               "description": "Phare : avant déploiement"}]}
        if v == "restaurer-snapshot":
            return {"ok": True, "restaure": a[1]}
        if v == "page-maintenance":
            return {"ok": True}
        if v in ("creer", "deployer"):
            jid = uuid.uuid4().hex[:12]
            self.jobs[jid] = {"debut": time.time(), "verbe": v}
            return {"ok": True, "job": jid}
        if v == "job":
            j = self.jobs.get(a[0], {"debut": 0, "verbe": "?"})
            dt = time.time() - j["debut"]
            etapes = ["> Création du CT 111", "> Démarrage", "> Installation des paquets (quelques minutes)",
                      "> Nom local dans Pi-hole", "> CT prêt."]
            n = min(len(etapes), int(dt / 1.5) + 1)
            return {"ok": True, "etat": "fini" if n == len(etapes) else "en cours", "verbe": j["verbe"], "journal": "\n".join(etapes[:n])}
        return {"ok": False, "erreur": f"démo : verbe {v} non simulé"}

    def _services(self, ctid: str) -> dict:
        s = next((x for x in _SITES if x[3] == ctid), None)
        if not s or self.etats.get(ctid) != "running":
            return {"ecoute": [], "unites": {}, "eteint": self.etats.get(ctid) != "running"}
        ecoute = [{"port": s[2], "processus": s[5].split(".")[0], "unite": s[5]}] if self.unites[ctid] == "running" else []
        return {"ecoute": ecoute, "unites": {s[5]: self.unites[ctid]}}


_D = None


def agent(commande: str, entree: bytes | None):
    global _D
    if _D is None:
        _D = _Demo()
    return _D.agent(commande, entree)
