"""Tests de phare-agent sans hôte Proxmox : réglages, contrôles d'arguments, liste des verbes."""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import unittest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT = os.path.join(RACINE, "agent", "phare-agent")

CONF = """\
# commentaire
NOEUD="noeud1"
RESEAU="10.1.2"            # trois octets
BLOC_SITES="100-149"
ID_MIN=200
CT_CADDY='301'
CT_PIHOLE=""
CT_PROTEGES="300, 302"
JOURNAL="{journal}"
"""


def charger(conf_texte):
    dossier = tempfile.mkdtemp()
    chemin = os.path.join(dossier, "agent.conf")
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(conf_texte.format(journal=os.path.join(dossier, "agent.log").replace("\\", "/")))
    os.environ["PHARE_AGENT_CONF"] = chemin
    try:
        chargeur = importlib.machinery.SourceFileLoader("phare_agent_essai", AGENT)
        spec = importlib.util.spec_from_loader(chargeur.name, chargeur)
        module = importlib.util.module_from_spec(spec)
        chargeur.exec_module(module)
        return module
    finally:
        del os.environ["PHARE_AGENT_CONF"]


class TestReglages(unittest.TestCase):
    def test_lecture_du_fichier(self):
        a = charger(CONF)
        self.assertEqual(a.NOEUD, "noeud1")
        self.assertEqual(a.RESEAU, "10.1.2")
        self.assertEqual(a.BLOC_SITES, (100, 149))
        self.assertEqual(a.ID_MIN, 200)
        self.assertEqual(a.CT_CADDY, "301")
        self.assertEqual(a.CT_PIHOLE, "")
        self.assertEqual(a.PASSERELLE, "10.1.2.1")  # par défaut : .1 du réseau

    def test_infra_toujours_protegee(self):
        a = charger(CONF)
        self.assertEqual(a.CT_PROTEGES, {"300", "302", "301", "103"})  # 103 : CT_PHARE par défaut

    def test_ip_du_reseau_seulement(self):
        a = charger(CONF)
        self.assertTrue(a.IP_RE.match("10.1.2.120"))
        self.assertFalse(a.IP_RE.match("10.1.20.1"))
        self.assertFalse(a.IP_RE.match("192.168.10.120"))

    def test_prochaine_ip_suit_la_plus_haute(self):
        a = charger(CONF)
        self.assertEqual(a.prochaine_ip_libre({"10.1.2.100", "10.1.2.104", "10.1.2.5"}), "10.1.2.105")
        self.assertEqual(a.prochaine_ip_libre(set()), "10.1.2.100")
        self.assertIsNone(a.prochaine_ip_libre({"10.1.2.149"}))

    def test_sans_fichier_valeurs_par_defaut(self):
        a = charger("")
        self.assertEqual((a.RESEAU, a.BLOC_SITES, a.ID_MIN), ("192.168.10", (120, 169), 110))


class TestVerbes(unittest.TestCase):
    def test_aucun_verbe_de_suppression(self):
        a = charger(CONF)
        for v in a.VERBES:
            self.assertNotRegex(v, r"suppr|detruire|destroy|delete")

    def test_verbe_inconnu_refuse(self):
        a = charger(CONF)
        os.environ["SSH_ORIGINAL_COMMAND"] = "rm -rf /"
        try:
            sortie = io.StringIO()
            with contextlib.redirect_stdout(sortie):
                code = a.main()
        finally:
            del os.environ["SSH_ORIGINAL_COMMAND"]
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(sortie.getvalue()), {"ok": False, "erreur": "Verbe inconnu."})

    def test_arguments_controles(self):
        a = charger(CONF)
        with self.assertRaises(a.Refus):
            a.v_ct(["105", "destroy"])
        with self.assertRaises(a.Refus):
            a.v_service(["105", "stop"])
        with self.assertRaises(a.Refus):
            a.v_pihole(["site", "10.1.2.120"])  # Pi-hole non configuré
        with self.assertRaises(a.Refus):
            a.v_job(["../../etc"])

    def test_ct_protege_refuse(self):
        a = charger(CONF)
        a.ct_existe = lambda ctid: True
        with self.assertRaises(a.Refus):
            a.verifier_ct("301")
        self.assertEqual(a.verifier_ct("301", pilotable=False), "301")
        self.assertEqual(a.verifier_ct("150"), "150")


if __name__ == "__main__":
    unittest.main()
