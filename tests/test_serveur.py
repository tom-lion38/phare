import unittest

from phare.moteur import ErreurPhare
from phare.serveur import nom_du_groupe, verifier_confirmation


class TestConfirmation(unittest.TestCase):
    def test_nom_exact_seulement(self):
        verifier_confirmation({"confirmation": "blog.exemple.fr"}, "blog.exemple.fr")
        verifier_confirmation({"confirmation": "  boutique  "}, "boutique")
        for saisie in ("", "blog", "BLOG.exemple.fr", None):
            with self.assertRaises(ErreurPhare):
                verifier_confirmation({"confirmation": saisie}, "blog.exemple.fr")
        with self.assertRaises(ErreurPhare):
            verifier_confirmation({}, "boutique")
        with self.assertRaises(ErreurPhare):
            verifier_confirmation({"confirmation": ""}, "")

    def test_nom_du_groupe(self):
        g = {"domaines": [{"nom": "a.lan", "lan": True}, {"nom": "a.exemple.fr", "lan": False}]}
        self.assertEqual(nom_du_groupe(g), "a.exemple.fr")
        self.assertEqual(nom_du_groupe({"domaines": [{"nom": "b.lan", "lan": True}]}), "b.lan")


if __name__ == "__main__":
    unittest.main()
