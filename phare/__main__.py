"""python -m phare [--demo] [--port N]"""

import argparse
import os

parser = argparse.ArgumentParser(prog="phare")
parser.add_argument("--demo", action="store_true", help="faux homelab en mémoire (rien n'est touché)")
parser.add_argument("--port", type=int, default=int(os.environ.get("PHARE_PORT", "80")))
parser.add_argument("--hote", default=os.environ.get("PHARE_HOTE", "0.0.0.0"))
args = parser.parse_args()

if args.demo:
    os.environ["PHARE_DEMO"] = "1"
    os.environ.setdefault("PHARE_DONNEES", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".demo-donnees"))

from .serveur import lancer  # noqa: E402  (après le réglage de l'environnement)

lancer(args.hote, args.port)
