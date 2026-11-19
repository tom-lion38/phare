import unittest

from phare import moteur

ETAT = {
    "invites": [
        {"id": "100", "type": "lxc", "nom": "pihole", "etat": "running", "cpu": 0.1, "ip": "192.168.10.3", "protege": True, "onboot": True},
        {"id": "200", "type": "qemu", "nom": "nas", "etat": "running", "cpu": 0.2},
    ],
    "noeud": {"cpu": 0.1, "cpus": 16, "mem": 1, "maxmem": 2, "charge": ["0.1", "0.2", "0.3"]},
    "prochain_id": "111", "presets": {"x": 1},
}
RESSOURCES = [
    {"type": "lxc", "vmid": 100, "name": "pihole", "status": "stopped", "cpu": 0, "maxcpu": 1, "mem": 0, "maxmem": 512, "disk": 1, "maxdisk": 8, "uptime": 0, "tags": "infra"},
    {"type": "qemu", "vmid": 200, "name": "nas", "status": "running", "cpu": 0.5, "maxcpu": 4, "mem": 3, "maxmem": 8, "disk": 0, "maxdisk": 32, "uptime": 99},
    {"type": "node", "node": "pve", "cpu": 0.3, "maxcpu": 16, "mem": 5, "maxmem": 64, "uptime": 1000},
    {"type": "storage", "storage": "local"},
]


class TestFusion(unittest.TestCase):
    def test_valeurs_mises_a_jour_et_reste_garde(self):
        n = moteur.fusionner_ressources(ETAT, RESSOURCES)
        p = n["invites"][0]
        self.assertEqual((p["etat"], p["maxmem"], p["tags"]), ("stopped", 512, "infra"))
        self.assertEqual((p["ip"], p["protege"], p["onboot"]), ("192.168.10.3", True, True))
        self.assertEqual(n["noeud"]["mem"], 5)
        self.assertEqual(n["noeud"]["charge"], ["0.1", "0.2", "0.3"])
        self.assertEqual((n["prochain_id"], n["presets"]), ("111", {"x": 1}))
        self.assertEqual(ETAT["invites"][0]["etat"], "running")  # l'original n'est pas modifié

    def test_liste_changee_demande_relecture(self):
        self.assertIsNone(moteur.fusionner_ressources(ETAT, RESSOURCES[:1] + RESSOURCES[2:]))
        plus = RESSOURCES + [{"type": "lxc", "vmid": 300, "name": "neuf"}]
        self.assertIsNone(moteur.fusionner_ressources(ETAT, plus))
        self.assertIsNone(moteur.fusionner_ressources({}, RESSOURCES))

    def test_meme_adresse_ancien_et_neuf(self):
        # un ancien CT gardé quelques jours, arrêté et protégé, sur la même adresse que son remplaçant
        ancien = {"id": "106", "type": "lxc", "nom": "blog", "etat": "stopped", "ip": "192.168.10.125",
                  "protege": True, "onboot": False}
        neuf = {"id": "110", "type": "lxc", "nom": "blog", "etat": "running", "ip": "192.168.10.125",
                "protege": False, "onboot": True}
        autre = {"id": "108", "type": "lxc", "nom": "api", "etat": "running", "ip": "192.168.10.121"}
        for ordre in ([ancien, neuf, autre], [neuf, ancien, autre]):
            e = moteur.Etat()
            e.etat = {"invites": ordre}
            self.assertEqual(e._ct_par_ip()["192.168.10.125"]["id"], "110")
            self.assertEqual(e._ct_par_ip()["192.168.10.121"]["id"], "108")
            self.assertEqual({i["id"] for i in e._cts_sites_sans_domaine()}, {"110", "108"})
        # les deux arrêtés : celui qui démarre seul l'emporte
        e = moteur.Etat()
        e.etat = {"invites": [ancien, dict(neuf, etat="stopped")]}
        self.assertEqual(e._ct_par_ip()["192.168.10.125"]["id"], "110")

    def test_cadences(self):
        self.assertEqual(set(moteur.PERIODES_ACTIF), set(moteur.PERIODES_REPOS))
        self.assertEqual(list(moteur.PERIODES_ACTIF)[0], "complet")
        self.assertEqual(moteur.PERIODES_ACTIF["sante"], 300)


class TestCategorie(unittest.TestCase):
    def test_blocs_par_defaut(self):
        self.assertEqual(moteur.categorie("192.168.10.120", None), "site")
        self.assertEqual(moteur.categorie("192.168.10.175", None), "appli")
        self.assertEqual(moteur.categorie("192.168.10.4", None), "infra")
        self.assertEqual(moteur.categorie(None, {"tags": "site;phare"}), "site")

    def test_reseau_donne_par_l_agent(self):
        reseau = {"prefixe": "10.1.2", "sites": [100, 149], "applis": [150, 159]}
        self.assertEqual(moteur.categorie("10.1.2.120", None, reseau), "site")
        self.assertEqual(moteur.categorie("10.1.2.155", None, reseau), "appli")
        self.assertEqual(moteur.categorie("10.1.2.40", None, reseau), "infra")
        self.assertEqual(moteur.categorie("192.168.10.120", None, reseau), "infra")


if __name__ == "__main__":
    unittest.main()
