"""
Partie Python de l'analyse par IA.

- contexte() rassemble ce que l'IA doit lire : README, liste des fichiers, extraits du code le plus utile,
  fichiers de configuration, et le résultat de l'analyse par règles (comme point de départ).
- valider() vérifie et nettoie la réponse de l'IA avant de s'en servir : on ne fait jamais confiance
  aveuglément à ce qu'elle renvoie (chemins qui n'existent pas, types inconnus, champs manquants…).
L'appel à l'IA lui-même est fait côté JavaScript (travailleur.js).
"""

import json
import os
import re

import analyseur

GENRES = {"texte", "fichier", "dossier", "sortie", "sortie_dossier", "case", "choix", "entier", "nombre"}
LIMITE_README = 8000
LIMITE_EXTRAIT = 7000
LIMITE_TOTAL = 36000
INDICES_CODE = re.compile(r"argparse|optparse|click|typer|fire\.Fire|sys\.argv|__main__|def main\b|input\(")


def _lire(chemin, limite):
    try:
        with open(chemin, encoding="utf-8", errors="replace") as f:
            texte = f.read(limite + 1)
    except OSError:
        return ""
    return texte if len(texte) <= limite else texte[:limite] + "\n[… coupé …]"


def contexte(racine, depot, description_github, regles_json):
    """Texte envoyé à l'IA pour qu'elle comprenne le projet."""
    fichiers, py = [], []
    for dossier, sous, noms in os.walk(racine):
        sous[:] = sorted(s for s in sous if not s.startswith("."))
        for n in sorted(noms):
            rel = os.path.relpath(os.path.join(dossier, n), racine).replace(os.sep, "/")
            fichiers.append(rel)
            if n.endswith(".py"):
                py.append(rel)

    # Les fichiers de code les plus parlants d'abord : points d'entrée déclarés, puis ceux qui lisent des arguments
    entrees = set()
    for cible in analyseur.scripts_declares(racine).values():
        f, _ = analyseur._module_vers_fichier(racine, cible.split(":")[0])
        if f:
            entrees.add(os.path.relpath(f, racine).replace(os.sep, "/"))

    def score(rel):
        texte = _lire(os.path.join(racine, rel), 60000)
        s = 100 if rel in entrees else 0
        s += 10 * len(INDICES_CODE.findall(texte))
        s += 15 if os.path.basename(rel) in ("__main__.py", "cli.py", "main.py", "app.py", "run.py") else 0
        s -= 3 * rel.count("/")
        return s

    choisis, total = [], 0
    utiles = [r for r in py if os.path.basename(r) not in ("setup.py", "conftest.py")
              and not re.match(r"test_|.*_test\.py$|test\.py$", os.path.basename(r))
              and not re.search(r"(^|/)(tests?|docs?|examples?)/", r)]
    notes = {r: score(r) for r in utiles}
    for rel in sorted(utiles, key=notes.get, reverse=True)[:8]:
        if notes[rel] <= 0 and choisis:
            break
        extrait = _lire(os.path.join(racine, rel), LIMITE_EXTRAIT)
        if total + len(extrait) > LIMITE_TOTAL:
            break
        choisis.append((rel, extrait))
        total += len(extrait)

    readme = ""
    for n in sorted(os.listdir(racine)):
        if n.lower().startswith("readme"):
            readme = _lire(os.path.join(racine, n), LIMITE_README)
            break

    config = []
    for n in ("pyproject.toml", "setup.cfg", "setup.py", "requirements.txt"):
        p = os.path.join(racine, n)
        if os.path.exists(p):
            config.append((n, _lire(p, 3000)))

    parties = [
        f"# Dépôt GitHub : {depot}",
        f"Description GitHub : {description_github or '(aucune)'}",
        "## Liste des fichiers récupérés (code et configuration uniquement)",
        "\n".join(fichiers[:300]) + ("\n[…]" if len(fichiers) > 300 else ""),
        "## Analyse automatique par règles (peut être incomplète ou fausse)",
        regles_json,
        "## README",
        readme or "(aucun README)",
    ]
    for n, t in config:
        parties += [f"## Fichier {n}", t]
    for rel, t in choisis:
        parties += [f"## Code : {rel}", t]
    return "\n\n".join(parties)


# ------------------------------------------------------------------ validation

def _texte(v, defaut="", limite=400):
    return str(v).strip()[:limite] if isinstance(v, (str, int, float)) and str(v).strip() else defaut


def _nom(v):
    n = re.sub(r"\W", "_", str(v or "")).strip("_")[:60]
    return n if n and not n[0].isdigit() else ("p_" + n if n else "")


def _module_existe(racine, module):
    if not isinstance(module, str) or not re.fullmatch(r"[A-Za-z_][\w.]*", module):
        return False
    f, _ = analyseur._module_vers_fichier(racine, module)
    return bool(f)


def _lancement(racine, l):
    if not isinstance(l, dict):
        return None
    t = l.get("type")
    base = _texte(l.get("base"), "", 200).strip("/")
    if ".." in base:
        return None
    if t == "script":
        module, fonction = l.get("module"), l.get("fonction")
        if _module_existe(racine, module) and isinstance(fonction, str) and re.fullmatch(r"[A-Za-z_][\w.]*", fonction):
            return {"type": "script", "nom": _texte(l.get("nom"), module.split(".")[0], 60),
                    "module": module, "fonction": fonction, "base": base}
    if t == "module" and _module_existe(racine, l.get("module")):
        return {"type": "module", "module": l["module"], "base": base}
    if t == "fichier":
        f = _texte(l.get("fichier"), "", 300).strip("/")
        if f and ".." not in f and f.endswith(".py") and os.path.isfile(os.path.join(racine, f)):
            return {"type": "fichier", "fichier": f}
    return None


def _param(p):
    if not isinstance(p, dict):
        return None
    nom = _nom(p.get("nom"))
    if not nom:
        return None
    positionnel = bool(p.get("positionnel"))
    drapeau = p.get("drapeau")
    if positionnel:
        drapeau = None
    else:
        drapeau = _texte(drapeau, "", 60)
        if not re.fullmatch(r"--?[\w][\w\-]*", drapeau):
            return None
    genre = p.get("genre") if p.get("genre") in GENRES else "texte"
    choix = p.get("choix")
    choix = [str(c)[:80] for c in choix][:50] if isinstance(choix, list) and choix else None
    if genre == "choix" and not choix:
        genre = "texte"
    if genre == "case" and positionnel:
        genre = "texte"
    defaut = p.get("defaut")
    if not isinstance(defaut, (str, int, float, bool)):
        defaut = None
    return {
        "nom": nom, "drapeau": drapeau, "positionnel": positionnel,
        "libelle": _texte(p.get("libelle"), analyseur._libelle(nom), 80),
        "aide": _texte(p.get("aide"), "", 400), "genre": genre,
        "inverse": bool(p.get("inverse")) and genre == "case",
        "defaut": defaut, "choix": choix, "requis": bool(p.get("requis")),
        "plusieurs": bool(p.get("plusieurs")), "avant": bool(p.get("avant")),
    }


def valider(racine, reponse_json, regles_json):
    """Retourne {"ok": True, "analyse": …} prêt à l'emploi, ou {"ok": False, "raison": …}."""
    try:
        r = json.loads(reponse_json) if isinstance(reponse_json, str) else reponse_json
    except ValueError:
        return json.dumps({"ok": False, "raison": "Réponse de l'IA illisible."})
    if not isinstance(r, dict):
        return json.dumps({"ok": False, "raison": "Réponse de l'IA illisible."})
    if not r.get("utilisable"):
        return json.dumps({"ok": False, "ia": True,
                           "raison": _texte(r.get("raison"), "L'IA estime que ce projet ne peut pas devenir une application.", 600)},
                          ensure_ascii=False)

    regles = json.loads(regles_json) if regles_json else {}
    lancement = _lancement(racine, r.get("lancement"))
    if not lancement and regles.get("ok"):
        lancement = regles["lancement"]  # l'IA s'est trompée de point d'entrée : on garde celui des règles
    if not lancement:
        return json.dumps({"ok": False, "raison": "L'IA n'a pas trouvé comment lancer ce projet."})

    outils = []
    for o in (r.get("outils") or [])[:12]:
        if not isinstance(o, dict):
            continue
        prefixe = [str(x)[:60] for x in o.get("prefixe") or [] if isinstance(x, (str, int))][:4]
        params, vus = [], set()
        for p in (o.get("params") or [])[:40]:
            q = _param(p)
            if q and q["nom"] not in vus:
                vus.add(q["nom"])
                params.append(q)
        params.sort(key=lambda p: (not p["avant"], not p["positionnel"]))
        nom = _texte(o.get("nom"), prefixe[0] if prefixe else "", 60)
        outils.append({"nom": nom, "prefixe": prefixe, "params": params,
                       "libelle": _texte(o.get("libelle"), analyseur._libelle(nom) if nom else "Utiliser l'outil", 80),
                       "aide": _texte(o.get("aide"), "", 300)})
    if not outils:
        return json.dumps({"ok": False, "raison": "L'IA n'a décrit aucune action utilisable."})

    analyse = {
        "ok": True, "source": "ia",
        "description": _texte(r.get("description"), regles.get("description", ""), 500),
        "style": regles.get("style") or "ia", "outils": outils, "lancement": lancement,
        "avertissements": [_texte(a, "", 300) for a in (r.get("avertissements") or [])[:3] if _texte(a)],
        "scripts": regles.get("scripts", {}),
    }
    return json.dumps({"ok": True, "analyse": analyse}, ensure_ascii=False)
