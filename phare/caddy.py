"""Lecture et modification du Caddyfile, en texte, sans jamais le réécrire en entier.

Phare ne touche qu'à ce qu'on lui demande : une ligne ``import maintenance`` ajoutée ou
retirée, l'adresse d'un ``reverse_proxy``, un bloc ajouté à la fin, un bloc retiré.
Tout le reste (commentaires, mise en forme, blocs écrits à la main) reste à l'identique.

Règles d'analyse (les mêmes que dans Abysse, la carte du homelab) :

- les snippets ``(xxx) { … }`` et le bloc d'options globales ``{ … }`` ne sont pas des sites ;
- ``a, b {`` déclare deux adresses dans un même bloc ;
- l'amont d'un bloc est le premier ``reverse_proxy`` qui s'applique à tout le site (pas de
  matcher, pas dans un ``handle /chemin``), à défaut le premier ``reverse_proxy`` du bloc ;
- une accolade n'ouvre ou ne ferme un bloc que si elle est un mot seul ; ``#`` en début de mot
  commence un commentaire ; les guillemets sont respectés.
"""

from __future__ import annotations

import re

SCHEMAS = ("https://", "http://", "h2c://")
MARQUEUR_SITES = "# ==== Sites ajoutés par Phare ===="
LIGNE_MAINTENANCE = "import maintenance"

SNIPPET_MAINTENANCE = """\
# ---- Page de maintenance (Phare) ----
# Un site « éteint » dans Phare reçoit la ligne « import maintenance » : tout le site
# répond par la page /etc/caddy/maintenance/index.html (code 503).
(maintenance) {
\thandle {
\t\troot * /etc/caddy/maintenance
\t\trewrite * /index.html
\t\theader Cache-Control "no-store"
\t\theader Retry-After "600"
\t\tfile_server {
\t\t\tstatus 503
\t\t}
\t}
}
"""

DOMAINE_RE = re.compile(r"^(?=.{1,200}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
IP_RE = re.compile(r"^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$")


class ErreurCaddy(ValueError):
    """Modification impossible (message en français, montrable tel quel)."""


# --------------------------------------------------------------------------- analyse

def mots(ligne: str) -> list[str]:
    """Découpe une ligne en mots (guillemets et accents graves respectés, commentaire retiré)."""
    res: list[str] = []
    i, n = 0, len(ligne)
    while i < n:
        c = ligne[i]
        if c.isspace():
            i += 1
            continue
        if c == "#":
            break
        mot = []
        while i < n and not ligne[i].isspace():
            c = ligne[i]
            if c in "\"`":
                fin = c
                mot.append(c)
                i += 1
                while i < n and ligne[i] != fin:
                    if ligne[i] == "\\" and fin == '"' and i + 1 < n:
                        mot.append(ligne[i])
                        i += 1
                    mot.append(ligne[i])
                    i += 1
                if i < n:
                    mot.append(ligne[i])
                    i += 1
                continue
            mot.append(c)
            i += 1
        res.append("".join(mot))
    return res


def _est_matcher(mot: str) -> bool:
    return mot.startswith(("@", "/", "*"))


def amont(adresse: str) -> tuple[str | None, int | None]:
    """``https://192.168.10.2:8006`` donne ``("192.168.10.2", 8006)`` ; ``192.168.10.6`` donne ``("192.168.10.6", 80)``."""
    a = adresse.strip()
    port_defaut = 80
    for schema in SCHEMAS:
        if a.lower().startswith(schema):
            a = a[len(schema):]
            port_defaut = 443 if schema == "https://" else 80
            break
    a = a.split("/", 1)[0]
    if not a:
        return None, None
    if a.count(":") == 1:
        hote, port_txt = a.split(":", 1)
    else:
        hote, port_txt = a, ""
    if not hote:
        return None, None
    if port_txt:
        if not port_txt.isdigit() or not 0 < int(port_txt) < 65536:
            return hote, None
        return hote, int(port_txt)
    return hote, port_defaut


def _adresses(mots_ouverture: list[str]) -> list[str]:
    res = []
    for morceau in " ".join(mots_ouverture).replace(",", " ").split():
        nom = morceau
        for schema in SCHEMAS:
            if nom.lower().startswith(schema):
                nom = nom[len(schema):]
                break
        nom = nom.rstrip("/")
        if nom:
            res.append(nom)
    return res


def blocs(texte: str) -> list[dict]:
    """Blocs de premier niveau. Chaque bloc : ``genre`` (site/snippet/global), ``debut`` et
    ``fin`` (index des lignes de l'accolade ouvrante et fermante), ``adresses``, ``amont``
    (texte brut du reverse_proxy choisi, ou None), ``ligne_amont``, ``maintenance``, ``trafic``.
    Un bloc ouvert et fermé sur la même ligne est ignoré (rare, jamais écrit par Phare)."""
    lignes = texte.splitlines()
    res: list[dict] = []
    pile: list[bool] = []  # conditionnel par niveau, pile[0] = le bloc lui-même
    courant: dict | None = None
    amonts: list[tuple[bool, str, int]] = []

    def directive(ms: list[str], num: int) -> None:
        if courant is None or courant["genre"] != "site" or not ms:
            return
        if len(pile) == 1:
            if ms == ["import", "maintenance"]:
                courant["maintenance"] = True
                courant["ligne_maintenance"] = num
            elif ms == ["import", "trafic"]:
                courant["trafic"] = True
        if ms[0] != "reverse_proxy":
            return
        args = ms[1:]
        cond = any(pile[1:])
        if args and _est_matcher(args[0]):
            cond = True
            args = args[1:]
        if args:
            amonts.append((cond, args[0], num))

    for num, ligne in enumerate(lignes):
        ms_ligne = mots(ligne)
        accu: list[str] = []
        for m in ms_ligne:
            if m == "{":
                if not pile:
                    if not accu:
                        genre = "global"
                    elif accu[0].startswith("("):
                        genre = "snippet"
                    else:
                        genre = "site"
                    courant = {
                        "genre": genre, "debut": num, "fin": None,
                        "adresses": _adresses(accu) if genre == "site" else [],
                        "nom_snippet": accu[0][1:-1] if genre == "snippet" else None,
                        "amont": None, "ligne_amont": None,
                        "maintenance": False, "ligne_maintenance": None, "trafic": False,
                    }
                    amonts = []
                    pile.append(False)
                else:
                    directive(accu, num)
                    cond = bool(accu) and (
                        accu[0] == "handle_errors" or accu[0].startswith("@")
                        or (len(accu) > 1 and _est_matcher(accu[1]))
                    )
                    pile.append(cond)
                accu = []
            elif m == "}":
                directive(accu, num)
                accu = []
                if not pile:
                    continue
                pile.pop()
                if not pile and courant is not None:
                    courant["fin"] = num
                    if courant["genre"] == "site":
                        choix = next((a for a in amonts if not a[0]), None) or (amonts[0] if amonts else None)
                        if choix:
                            courant["amont"], courant["ligne_amont"] = choix[1], choix[2]
                    if courant["debut"] != num:
                        res.append(courant)
                    courant = None
            else:
                accu.append(m)
        directive(accu, num)
    return res


def sites(texte: str) -> list[dict]:
    """Un élément par bloc de site : adresses, amont (ip, port), maintenance, trafic."""
    res = []
    for b in blocs(texte):
        if b["genre"] != "site":
            continue
        ip, port = amont(b["amont"]) if b["amont"] else (None, None)
        res.append({
            "adresses": b["adresses"],
            "amont_brut": b["amont"],
            "amont_ip": ip,
            "amont_port": port,
            "maintenance": b["maintenance"],
            "trafic": b["trafic"],
            "phare": False,
        })
    # Les blocs placés après le marqueur ont été créés par Phare.
    lignes = texte.splitlines()
    try:
        idx = next(i for i, l in enumerate(lignes) if l.strip() == MARQUEUR_SITES)
    except StopIteration:
        return res
    for s, b in zip(res, [b for b in blocs(texte) if b["genre"] == "site"]):
        s["phare"] = b["debut"] > idx
    return res


def sites_pour_abysse(texte: str) -> list[dict]:
    """Même format que ``sites.json`` d'Abysse : un élément par nom de site ouvrable."""
    res, vus = [], set()
    for s in sites(texte):
        for nom in s["adresses"]:
            if nom.startswith(":") or "*" in nom or "{" in nom or '"' in nom or nom in vus:
                continue
            vus.add(nom)
            res.append({"nom": nom, "url": "https://" + nom, "amont_ip": s["amont_ip"], "amont_port": s["amont_port"]})
    return res


# --------------------------------------------------------------------------- modifications

def _bloc_de(texte: str, adresse: str) -> dict:
    for b in blocs(texte):
        if b["genre"] == "site" and adresse in b["adresses"]:
            return b
    raise ErreurCaddy(f"Le domaine {adresse} n'est pas dans le Caddyfile.")


def _fin_de_ligne(texte: str) -> str:
    return "\r\n" if "\r\n" in texte else "\n"


def _joindre(lignes: list[str], nl: str, texte_orig: str) -> str:
    sortie = nl.join(lignes)
    if texte_orig.endswith(("\n", "\r\n")):
        sortie += nl
    return sortie


def assurer_snippet_maintenance(texte: str) -> str:
    """Ajoute le snippet ``(maintenance)`` s'il manque, juste avant le premier bloc de site
    (un snippet doit être défini avant d'être importé)."""
    if any(b["genre"] == "snippet" and b["nom_snippet"] == "maintenance" for b in blocs(texte)):
        return texte
    nl = _fin_de_ligne(texte)
    lignes = texte.splitlines()
    premier_site = next((b["debut"] for b in blocs(texte) if b["genre"] == "site"), len(lignes))
    # remonter au-dessus des commentaires collés au premier site
    i = premier_site
    while i > 0 and lignes[i - 1].lstrip().startswith("#"):
        i -= 1
    ajout = SNIPPET_MAINTENANCE.rstrip("\n").split("\n") + [""]
    return _joindre(lignes[:i] + ajout + lignes[i:], nl, texte)


def maintenance(texte: str, adresse: str, active: bool) -> str:
    """Met (ou retire) ``import maintenance`` dans le bloc du domaine."""
    texte = assurer_snippet_maintenance(texte) if active else texte
    b = _bloc_de(texte, adresse)
    if b["maintenance"] == active:
        return texte
    nl = _fin_de_ligne(texte)
    lignes = texte.splitlines()
    if active:
        retrait = "\t"
        for l in lignes[b["debut"] + 1:b["fin"]]:
            if l.strip():
                retrait = l[: len(l) - len(l.lstrip())] or "\t"
                break
        lignes.insert(b["debut"] + 1, retrait + LIGNE_MAINTENANCE + "  # Phare : site éteint")
    else:
        del lignes[b["ligne_maintenance"]]
    return _joindre(lignes, nl, texte)


def changer_amont(texte: str, adresse: str, ip: str, port: int) -> str:
    """Remplace l'adresse du reverse_proxy principal du bloc (le schéma éventuel est gardé)."""
    if not IP_RE.match(ip):
        raise ErreurCaddy("Adresse IP invalide.")
    if not isinstance(port, int) or not 0 < port < 65536:
        raise ErreurCaddy("Port invalide.")
    b = _bloc_de(texte, adresse)
    if b["ligne_amont"] is None:
        raise ErreurCaddy(f"Le bloc de {adresse} n'a pas de reverse_proxy à modifier.")
    nl = _fin_de_ligne(texte)
    lignes = texte.splitlines()
    ancienne = b["amont"]
    schema = next((s for s in SCHEMAS if ancienne.lower().startswith(s)), "")
    nouvelle = f"{schema}{ip}:{port}"
    l = lignes[b["ligne_amont"]]
    pos = l.find(ancienne, l.find("reverse_proxy"))
    if pos < 0:
        raise ErreurCaddy("Ligne reverse_proxy introuvable.")
    lignes[b["ligne_amont"]] = l[:pos] + nouvelle + l[pos + len(ancienne):]
    return _joindre(lignes, nl, texte)


def valider_domaine(domaine: str) -> str:
    d = (domaine or "").strip().lower().rstrip(".")
    if not DOMAINE_RE.match(d):
        raise ErreurCaddy("Nom de domaine invalide (exemple : monsite.exemple.fr).")
    return d


def ajouter_site(texte: str, domaine: str, ip: str, port: int, commentaire: str = "") -> str:
    """Ajoute un bloc ``domaine { reverse_proxy ip:port }`` après le marqueur Phare, avec
    ``import trafic``, ``import securite`` et ``import journal`` si ces extraits existent."""
    domaine = valider_domaine(domaine)
    if not IP_RE.match(ip):
        raise ErreurCaddy("Adresse IP invalide.")
    if not isinstance(port, int) or not 0 < port < 65536:
        raise ErreurCaddy("Port invalide.")
    for b in blocs(texte):
        if b["genre"] == "site" and domaine in b["adresses"]:
            raise ErreurCaddy(f"{domaine} est déjà dans le Caddyfile.")
    commentaire = re.sub(r"[\r\n]+", " ", commentaire or "").strip()[:120]
    nl = _fin_de_ligne(texte)
    lignes = texte.splitlines()
    while lignes and not lignes[-1].strip():
        lignes.pop()
    if not any(l.strip() == MARQUEUR_SITES for l in lignes):
        lignes += ["", MARQUEUR_SITES]
    bloc = [""]
    if commentaire:
        bloc.append("# " + commentaire)
    bloc += [f"{domaine} {{"]
    # Extraits communs, seulement s'ils sont définis dans le Caddyfile (sinon Caddy refuserait l'import) :
    # (trafic) pour les statistiques, (securite) pour les en-têtes, (journal) pour un journal par site.
    if any(l.strip().startswith("(trafic)") for l in lignes):
        bloc.append("\timport trafic")
    if any(l.strip().startswith("(securite)") for l in lignes):
        bloc.append("\timport securite")
    if any(l.strip().startswith("(journal)") for l in lignes):
        bloc.append("\timport journal " + domaine.replace(".", "-"))
    bloc += [f"\treverse_proxy {ip}:{port}", "}"]
    return nl.join(lignes + bloc) + nl


def retirer_site(texte: str, domaine: str) -> str:
    """Retire un domaine : tout le bloc s'il est seul, sinon juste son nom dans l'en-tête."""
    b = _bloc_de(texte, domaine)
    nl = _fin_de_ligne(texte)
    lignes = texte.splitlines()
    if len(b["adresses"]) > 1:
        l = lignes[b["debut"]]
        tete, _, reste = l.partition("{")
        noms = [n for n in re.split(r"[,\s]+", tete.strip()) if n]
        garde = [n for n in noms if n.rstrip("/").split("://")[-1] != domaine]
        lignes[b["debut"]] = ", ".join(garde) + " {" + reste
        return _joindre(lignes, nl, texte)
    debut = b["debut"]
    # emporter les commentaires collés juste au-dessus
    while debut > 0 and lignes[debut - 1].lstrip().startswith("#") and lignes[debut - 1].strip() != MARQUEUR_SITES:
        debut -= 1
    fin = b["fin"]
    if fin + 1 < len(lignes) and not lignes[fin + 1].strip() and debut > 0 and not lignes[debut - 1].strip():
        fin += 1
    del lignes[debut:fin + 1]
    return _joindre(lignes, nl, texte)
