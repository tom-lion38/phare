import os
import unittest

from phare import caddy

EXEMPLE = """\
{
\temail admin@exemple.fr
}
# journal
(trafic) {
\tlog trafic {
\t\tformat json
\t}
}

# site principal
exemple.fr {
\timport trafic
\thandle /api/* {
\t\treverse_proxy 192.168.10.17:8099
\t}
\thandle {
\t\treverse_proxy 192.168.10.120:80
\t}
}

pve.exemple.fr {
\treverse_proxy https://192.168.10.2:8006 {
\t\ttransport http {
\t\t\ttls_insecure_skip_verify
\t\t}
\t}
}

a.lan, b.lan {
\treverse_proxy 192.168.10.130:3000 # commentaire {
}
"""


class TestCaddy(unittest.TestCase):
    def test_sites(self):
        s = caddy.sites(EXEMPLE)
        self.assertEqual([x["adresses"] for x in s], [["exemple.fr"], ["pve.exemple.fr"], ["a.lan", "b.lan"]])
        self.assertEqual((s[0]["amont_ip"], s[0]["amont_port"]), ("192.168.10.120", 80))
        self.assertEqual((s[1]["amont_ip"], s[1]["amont_port"]), ("192.168.10.2", 8006))
        self.assertTrue(s[0]["trafic"])

    def test_maintenance_aller_retour(self):
        t = caddy.maintenance(EXEMPLE, "exemple.fr", True)
        self.assertIn("(maintenance) {", t)
        self.assertLess(t.index("(maintenance) {"), t.index("exemple.fr {"))
        self.assertTrue(caddy.sites(t)[0]["maintenance"])
        t2 = caddy.maintenance(t, "exemple.fr", False)
        self.assertFalse(caddy.sites(t2)[0]["maintenance"])
        # seul le snippet reste en plus
        self.assertEqual(t2.replace(caddy.SNIPPET_MAINTENANCE + chr(10), ""), EXEMPLE)
        # idempotent
        self.assertEqual(caddy.maintenance(t, "exemple.fr", True), t)

    def test_changer_amont_garde_le_schema(self):
        t = caddy.changer_amont(EXEMPLE, "pve.exemple.fr", "192.168.10.12", 8007)
        self.assertIn("reverse_proxy https://192.168.10.12:8007 {", t)
        t = caddy.changer_amont(EXEMPLE, "exemple.fr", "192.168.10.132", 3000)
        self.assertIn("reverse_proxy 192.168.10.132:3000", t)
        self.assertIn("reverse_proxy 192.168.10.17:8099", t)

    def test_ajouter_retirer(self):
        t = caddy.ajouter_site(EXEMPLE, "Nouveau.exemple.fr", "192.168.10.132", 80, "test")
        s = caddy.sites(t)
        self.assertEqual(s[-1]["adresses"], ["nouveau.exemple.fr"])
        self.assertTrue(s[-1]["phare"])
        self.assertTrue(s[-1]["trafic"])
        self.assertFalse(s[0]["phare"])
        with self.assertRaises(caddy.ErreurCaddy):
            caddy.ajouter_site(t, "nouveau.exemple.fr", "192.168.10.132", 80)
        t2 = caddy.retirer_site(t, "nouveau.exemple.fr")
        self.assertNotIn("nouveau.exemple.fr", t2)
        self.assertNotIn("# test", t2)

    def test_ajouter_sans_extrait_trafic(self):
        # sans snippet (trafic) dans le Caddyfile, pas d'« import trafic » (Caddy le refuserait)
        nu = "a.exemple.fr {\n\treverse_proxy 192.168.10.120:80\n}\n"
        t = caddy.ajouter_site(nu, "b.exemple.fr", "192.168.10.121", 80)
        self.assertNotIn("import trafic", t)
        self.assertEqual(caddy.sites(t)[-1]["amont_ip"], "192.168.10.121")

    def test_ajouter_avec_extraits_communs(self):
        base = EXEMPLE + "\n(securite) {\n\theader -Server\n}\n(journal) {\n\tlog {args[0]} {\n\t}\n}\n"
        t = caddy.ajouter_site(base, "neuf.exemple.fr", "192.168.10.133", 80)
        bloc = t[t.index("neuf.exemple.fr {"):]
        self.assertIn("\timport securite", bloc)
        self.assertIn("\timport journal neuf-exemple-fr", bloc)
        t2 = caddy.ajouter_site(EXEMPLE, "nu.exemple.fr", "192.168.10.134", 80)
        self.assertNotIn("import securite", t2[t2.index("nu.exemple.fr {"):])

    def test_retirer_une_adresse_parmi_deux(self):
        t = caddy.retirer_site(EXEMPLE, "b.lan")
        self.assertIn("a.lan {", t)
        self.assertEqual(caddy.sites(t)[-1]["adresses"], ["a.lan"])

    def test_refus(self):
        with self.assertRaises(caddy.ErreurCaddy):
            caddy.ajouter_site(EXEMPLE, "pas un domaine", "192.168.10.1", 80)
        with self.assertRaises(caddy.ErreurCaddy):
            caddy.changer_amont(EXEMPLE, "exemple.fr", "192.168.10.300", 80)
        with self.assertRaises(caddy.ErreurCaddy):
            caddy.maintenance(EXEMPLE, "inconnu.fr", True)

    @unittest.skipUnless(os.environ.get("CADDYFILE_REEL"), "CADDYFILE_REEL non défini")
    def test_caddyfile_reel(self):
        """Avec une copie de ton vrai Caddyfile : la maintenance doit faire l'aller-retour sur chaque domaine
        sans rien changer d'autre. Exemple : CADDYFILE_REEL=/tmp/Caddyfile python -m unittest tests.test_caddy"""
        with open(os.environ["CADDYFILE_REEL"], encoding="utf-8") as f:
            reel = f.read()
        noms = [a for x in caddy.sites(reel) for a in x["adresses"]]
        self.assertTrue(noms)
        for dom in noms:
            if "." not in dom or dom.startswith(":") or "*" in dom:
                continue
            t = caddy.maintenance(reel, dom, True)
            self.assertEqual(caddy.maintenance(t, dom, False).replace(caddy.SNIPPET_MAINTENANCE + chr(10), ""), reel, dom)


if __name__ == "__main__":
    unittest.main()
