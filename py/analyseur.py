"""
Analyseur de dépôts : lit le code Python d'un projet (sans jamais l'exécuter)
et devine ce que l'outil sait faire et quelles options il accepte,
pour pouvoir générer une interface graphique.

Reconnaît : argparse (avec sous-commandes), optparse et click (avec groupes de commandes).
Le résultat est un dictionnaire simple ; une future version pourra le compléter avec une IA.
"""

import ast
import os
import re

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    tomllib = None

# Mots qui indiquent qu'une option attend un fichier à fournir
INDICES_FICHIER = ("file", "path", "input", "image", "img", "photo", "picture", "audio", "video",
                   "pdf", "src", "source", "fichier", "csv", "infile", "inputs", "files", "images")
# Mots qui indiquent qu'une option désigne un fichier ou un dossier de sortie
INDICES_SORTIE = ("output", "out", "dest", "destination", "save", "target", "sortie", "outfile",
                  "outdir", "output_dir", "result")
DOSSIERS_IGNORES = {".git", "venv", ".venv", "env", "node_modules", "__pycache__", "build", "dist",
                    "site-packages", ".tox", "docs", "doc", "examples", "example", "tests", "test",
                    "benchmarks", "scripts_dev"}
TAILLE_MAX_FICHIER = 600_000


# ------------------------------------------------------------------ utilitaires

def _valeur(node):
    """Valeur d'un nœud du code, quand on peut la connaître sans exécuter le programme."""
    try:
        return ast.literal_eval(node)
    except Exception:
        pass
    if isinstance(node, ast.Name):
        return {"__nom__": node.id}
    if isinstance(node, ast.Attribute):
        return {"__nom__": node.attr}
    if isinstance(node, ast.Call):
        f = node.func
        nom = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
        return {"__appel__": nom, "args": [_valeur(a) for a in node.args],
                "kw": {k.arg: _valeur(k.value) for k in node.keywords if k.arg}}
    return None


def _nom_type(v):
    if isinstance(v, dict):
        return v.get("__nom__") or v.get("__appel__") or ""
    if isinstance(v, str):
        return v  # optparse : type="int"
    return ""


def _nettoyer(texte, prog=""):
    if not isinstance(texte, str):
        return ""
    texte = re.sub(r"%\((\w+)\)s", lambda m: "valeur par défaut" if m.group(1) == "default" else m.group(1), texte)
    texte = texte.replace("%default", "valeur par défaut").replace("%prog", prog or "programme").replace("%%", "%")
    return " ".join(texte.split())


def _libelle(nom):
    nom = nom.strip("-").replace("_", " ").replace("-", " ").strip()
    return (nom[:1].upper() + nom[1:]) if nom else "Valeur"


def _contient(nom, indices):
    n = nom.lower()
    mots = re.split(r"[_\-\s]+", n)
    return n in indices or any(i in mots for i in indices) or any(n.endswith(i) for i in indices if len(i) > 3)


INDICES_DOSSIER = ("dir", "folder", "directory", "dossier", "outdir", "output_dir", "input_dir")


def _deviner_genre(nom, aide, type_nom, action, choix, defaut, dossier=False):
    """Choisit le type de champ à afficher dans l'interface."""
    g = _genre_base(nom, aide, type_nom, action, choix, defaut)
    if g in ("fichier", "sortie") and (dossier or _contient(nom, INDICES_DOSSIER)):
        return "dossier" if g == "fichier" else "sortie_dossier"
    return g


MOTS_REGLAGE = ("format", "type", "mode", "level", "encoding", "style", "size", "count", "width", "height", "lang")


def _genre_base(nom, aide, type_nom, action, choix, defaut):
    if action in ("store_true", "store_false", "store_const", "is_flag"):
        return "case"
    if action == "count":
        return "entier"
    if choix:
        return "choix"
    t = (type_nom or "").lower()
    if t in ("filetype", "path", "file") or t.endswith("path"):
        return "sortie" if _contient(nom, INDICES_SORTIE) else "fichier"
    if t in ("int", "intrange"):
        return "entier"
    if t in ("float", "floatrange"):
        return "nombre"
    reglage = any(m in re.split(r"[_\-\s]+", nom.lower()) for m in MOTS_REGLAGE)
    aide_l = (aide or "").lower()
    if not reglage and (_contient(nom, INDICES_SORTIE) or re.search(r"\b(write|save)s? (the )?(output|result)s?\b.*\bto\b|\boutput[_ ]file\b", aide_l)):
        return "sortie"
    if not reglage and _contient(nom, INDICES_FICHIER):
        return "fichier"
    if isinstance(defaut, bool):
        return "case"
    if isinstance(defaut, int):
        return "entier"
    if isinstance(defaut, float):
        return "nombre"
    return "texte"


def _plusieurs(nargs):
    return nargs in ("+", "*", -1) or (isinstance(nargs, int) and nargs > 1)


def _param(nom, drapeau, positionnel, aide, genre, defaut, choix, requis, plusieurs, inverse=False):
    return {
        "nom": nom, "drapeau": drapeau, "positionnel": positionnel, "libelle": _libelle(nom),
        "aide": aide, "genre": genre, "inverse": inverse,
        "defaut": defaut if isinstance(defaut, (str, int, float, bool)) else None,
        "choix": choix, "requis": bool(requis), "plusieurs": bool(plusieurs), "avant": False,
    }


# ------------------------------------------------------------------ argparse / optparse

def _depuis_argparse(appel, prog=""):
    drapeaux = [v for v in (_valeur(a) for a in appel.args) if isinstance(v, str)]
    if not drapeaux:
        return None
    kw = {k.arg: _valeur(k.value) for k in appel.keywords if k.arg}
    action = kw.get("action") if isinstance(kw.get("action"), str) else ""
    if action in ("help", "version") or any(d in ("-h", "--help", "--version", "-V") for d in drapeaux):
        return None
    if kw.get("help") == {"__nom__": "SUPPRESS"}:
        return None
    positionnel = not drapeaux[0].startswith("-")
    longs = [d for d in drapeaux if d.startswith("--")]
    dest = kw.get("dest") if isinstance(kw.get("dest"), str) else None
    nom = (dest or (drapeaux[0] if positionnel else (longs[0] if longs else drapeaux[0]).lstrip("-"))).replace("-", "_")
    aide = _nettoyer(kw.get("help"), prog)
    choix = kw.get("choices") if isinstance(kw.get("choices"), (list, tuple)) else None
    defaut = kw.get("default")
    if isinstance(defaut, dict):
        defaut = None
    nargs = kw.get("nargs")
    requis = (positionnel and nargs not in ("?", "*")) or kw.get("required") is True
    genre = _deviner_genre(nom, aide, _nom_type(kw.get("type")), action, choix, defaut)
    return _param(nom, None if positionnel else (longs[0] if longs else drapeaux[0]), positionnel, aide,
                  genre, defaut, [str(c) for c in choix] if choix else None, requis,
                  _plusieurs(nargs) or action == "append", inverse=(action == "store_false"))


def _cible(node):
    """Nom de la variable sur laquelle une méthode est appelée (parser.add_argument → 'parser')."""
    v = node.func.value if isinstance(node.func, ast.Attribute) else None
    if isinstance(v, ast.Name):
        return v.id
    if isinstance(v, ast.Attribute):
        return v.attr
    return None


def _outils_argparse(arbre, prog):
    """Retourne les 'outils' définis avec argparse ou optparse dans un fichier."""
    analyseurs = {}     # variable → ("principal", None, aide) ou ("sous", nom, aide)
    alias = {}          # groupe d'arguments → analyseur parent
    description, usage, style, a_sous = "", "", None, False

    for node in ast.walk(arbre):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        appel = node.value
        f = appel.func
        meth = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
        noms = [t.id for t in node.targets if isinstance(t, ast.Name)] + \
               [t.attr for t in node.targets if isinstance(t, ast.Attribute)]
        if not noms:
            continue
        kw = {k.arg: _valeur(k.value) for k in appel.keywords if k.arg}
        if meth in ("ArgumentParser", "OptionParser"):
            for n in noms:
                analyseurs[n] = ("principal", None, "")
            if isinstance(kw.get("description"), str) and not description:
                description = _nettoyer(kw["description"], prog)
            if isinstance(kw.get("usage"), str) and not usage:
                usage = _nettoyer(kw["usage"], prog)
        elif meth == "add_parser":
            args = [_valeur(a) for a in appel.args]
            if args and isinstance(args[0], str):
                aide = kw.get("help") or kw.get("description") or ""
                for n in noms:
                    analyseurs[n] = ("sous", args[0], _nettoyer(aide, prog))
        elif meth in ("add_argument_group", "add_mutually_exclusive_group", "add_option_group", "OptionGroup"):
            parent = _cible(appel)
            if meth == "OptionGroup" and appel.args and isinstance(appel.args[0], ast.Name):
                parent = appel.args[0].id
            for n in noms:
                alias[n] = parent

    principaux, sous, ordre_sous = [], {}, []
    if not usage or not description:
        # usage=usage / description=desc : le texte est rangé dans une variable écrite plus haut
        chaines = {}
        for node in ast.walk(arbre):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        chaines[t.id] = node.value.value
        for node in ast.walk(arbre):
            if isinstance(node, ast.Call):
                f = node.func
                meth = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
                if meth in ("ArgumentParser", "OptionParser"):
                    for k in node.keywords:
                        if isinstance(k.value, ast.Name) and k.value.id in chaines:
                            if k.arg == "usage" and not usage:
                                usage = _nettoyer(chaines[k.value.id], prog)
                            elif k.arg == "description" and not description:
                                description = _nettoyer(chaines[k.value.id], prog)
    for node in ast.walk(arbre):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        meth = node.func.attr
        if meth == "add_subparsers":
            a_sous = True
        if meth not in ("add_argument", "add_option"):
            continue
        p = _depuis_argparse(node, prog)
        if not p:
            continue
        style = style or ("optparse" if meth == "add_option" else "argparse")
        v = _cible(node)
        for _ in range(5):
            if v in alias:
                v = alias[v]
        info = analyseurs.get(v, ("principal", None, ""))
        if info[0] == "sous":
            if info[1] not in sous:
                sous[info[1]] = {"aide": info[2], "params": []}
                ordre_sous.append(info[1])
            liste = sous[info[1]]["params"]
        else:
            liste = principaux
        if p["nom"] not in {x["nom"] for x in liste}:
            liste.append(p)

    if style == "optparse":
        # optparse ne déclare pas le texte libre qu'on écrit après les options : on ajoute un champ pour lui
        principaux.append(_param("arguments", None, True,
                                 f"Ce qu'on écrit après les options. Mode d'emploi : {usage}" if usage else
                                 "Le texte ou les éléments à donner à l'outil.",
                                 "texte", None, None, False, False))
        principaux[-1]["libelle"] = "Texte à traiter"
        if re.search(r"\[?\b(input[_ ]?file|infile|file(name)?s?|path)\b", usage, re.I):
            principaux[-1].update(genre="fichier", libelle="Fichier à traiter")

    if not principaux and not sous:
        return [], description, style
    if a_sous and sous:
        communs = [dict(p, avant=True) for p in principaux]
        outils = [{"nom": n, "aide": sous[n]["aide"], "prefixe": [n], "params": communs + sous[n]["params"]}
                  for n in ordre_sous]
    else:
        outils = [{"nom": "", "aide": description, "prefixe": [], "params": principaux}]
    return outils, description, style


# ------------------------------------------------------------------ click

def _depuis_click(deco):
    f = deco.func
    sorte = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
    if sorte not in ("option", "argument"):
        return None
    drapeaux = [v for v in (_valeur(a) for a in deco.args) if isinstance(v, str)]
    if not drapeaux:
        return None
    kw = {k.arg: _valeur(k.value) for k in deco.keywords if k.arg}
    if kw.get("hidden") is True:
        return None
    if sorte == "argument":
        nom, positionnel, drapeau = drapeaux[0], True, None
    else:
        vrais = [d for d in drapeaux if d.startswith("-")]
        if not vrais or any(d in ("--help", "--version") for d in vrais):
            return None
        noms = [d for d in drapeaux if not d.startswith("-")]
        longs = [d.split("/")[0] for d in vrais if d.startswith("--")]
        drapeau = longs[0] if longs else vrais[0].split("/")[0]
        nom, positionnel = (noms[0] if noms else drapeau.lstrip("-")), False
    nom = nom.replace("-", "_")
    typ = kw.get("type")
    choix = None
    if isinstance(typ, dict) and typ.get("__appel__") == "Choice" and typ.get("args"):
        if isinstance(typ["args"][0], (list, tuple)):
            choix = [str(c) for c in typ["args"][0]]
    est_case = kw.get("is_flag") is True or any("/" in d for d in drapeaux if d.startswith("-"))
    defaut = kw.get("default")
    if isinstance(defaut, dict):
        defaut = None
    aide = _nettoyer(kw.get("help"))
    nargs = kw.get("nargs")
    requis = kw.get("required") is True or (positionnel and kw.get("required") is not False
                                            and nargs != -1 and defaut is None)
    dossier = isinstance(typ, dict) and typ.get("__appel__") == "Path" and \
        (typ.get("kw") or {}).get("file_okay") is False
    genre = _deviner_genre(nom, aide, _nom_type(typ), "is_flag" if est_case else "", choix, defaut, dossier)
    return _param(nom, drapeau, positionnel, aide, genre, defaut, choix, requis,
                  _plusieurs(nargs) or kw.get("multiple") is True)


def _commandes_click(arbre):
    """Commandes click d'un fichier → liste de {nom, aide, params, groupe}."""
    commandes = []
    for node in ast.walk(arbre):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        sorte, nom_cmd, aide = None, None, ""
        for d in node.decorator_list:
            f = d.func if isinstance(d, ast.Call) else d
            attr = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if attr in ("command", "group"):
                sorte = attr
                if isinstance(d, ast.Call):
                    kw = {k.arg: _valeur(k.value) for k in d.keywords if k.arg}
                    args = [_valeur(a) for a in d.args]
                    nom_cmd = kw.get("name") if isinstance(kw.get("name"), str) else \
                        (args[0] if args and isinstance(args[0], str) else None)
                    aide = kw.get("help") if isinstance(kw.get("help"), str) else ""
        if not sorte:
            continue
        params = [p for p in (_depuis_click(d) for d in node.decorator_list if isinstance(d, ast.Call)) if p]
        if not aide:
            doc = ast.get_docstring(node) or ""
            aide = doc.split("\n\n")[0]
        nom_cmd = nom_cmd or re.sub(r"_(command|cmd|cli)$", "", node.name).replace("_", "-")
        commandes.append({"nom": nom_cmd, "aide": _nettoyer(aide), "params": params, "groupe": sorte == "group"})
    return commandes


# ------------------------------------------------------------------ fichiers et dépôt

def _lire_arbre(chemin):
    try:
        if os.path.getsize(chemin) > TAILLE_MAX_FICHIER:
            return None, ""
        with open(chemin, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
        return ast.parse(source), source
    except Exception:
        return None, ""


def analyser_fichier(chemin, prog=""):
    arbre, source = _lire_arbre(chemin)
    if arbre is None:
        return None
    outils, description, style = _outils_argparse(arbre, prog)
    commandes = _commandes_click(arbre)
    a_main = bool(re.search(r"""if\s+__name__\s*==\s*['"]__main__['"]""", source))
    return {"outils": outils, "description": description, "style": style,
            "click": commandes, "a_main": a_main}


def _lire_toml(racine):
    pp = os.path.join(racine, "pyproject.toml")
    if not os.path.exists(pp) or not tomllib:
        return {}
    try:
        with open(pp, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def scripts_declares(racine):
    """Commandes déclarées par le projet → {nom: 'module:fonction'}."""
    scripts = {}
    data = _lire_toml(racine)
    scripts.update(data.get("project", {}).get("scripts", {}) or {})
    scripts.update(data.get("tool", {}).get("poetry", {}).get("scripts", {}) or {})
    for nom in ("setup.cfg", "setup.py"):
        p = os.path.join(racine, nom)
        if os.path.exists(p):
            try:
                texte = open(p, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            if "console_scripts" in texte:
                for m in re.finditer(r"""['"]?\s*([\w\-.]+)\s*=\s*([\w.]+):([\w.]+)\s*['"]?""", texte):
                    scripts.setdefault(m.group(1), f"{m.group(2)}:{m.group(3)}")
    return {k: v for k, v in scripts.items() if isinstance(v, str) and ":" in v}


def _module_vers_fichier(racine, module):
    parties = module.split(".")
    for base in ("", "src"):
        candidat = os.path.join(racine, base, *parties)
        if os.path.isfile(candidat + ".py"):
            return os.path.normpath(candidat + ".py"), base
        if os.path.isfile(os.path.join(candidat, "__init__.py")):
            return os.path.normpath(os.path.join(candidat, "__init__.py")), base
    return None, None


def _module_python(racine, fichier):
    """Nom de module d'un fichier rangé dans un paquet (dossier avec __init__.py)."""
    dossier = os.path.dirname(fichier)
    if not os.path.exists(os.path.join(dossier, "__init__.py")):
        return None
    parties = [os.path.splitext(os.path.basename(fichier))[0]]
    while os.path.exists(os.path.join(dossier, "__init__.py")):
        parties.insert(0, os.path.basename(dossier))
        dossier = os.path.dirname(dossier)
    if parties[-1] == "__main__":
        parties.pop()
    base = os.path.relpath(dossier, racine)
    return ".".join(parties), ("" if base == "." else base.replace(os.sep, "/"))


def lire_readme(racine):
    for nom in sorted(os.listdir(racine)):
        if not nom.lower().startswith("readme"):
            continue
        try:
            texte = open(os.path.join(racine, nom), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        texte = re.sub(r"```.*?```", " ", texte, flags=re.S)
        texte = re.sub(r"^\s*\[[^\]]+\]:\s*\S+.*$", "", texte, flags=re.M)  # définitions de liens
        for bloc in re.split(r"\n\s*\n", texte):
            b = re.sub(r"<[^>]+>", " ", bloc)
            b = re.sub(r"\[!\[[^\]]*\](\[[^\]]*\]|\([^)]*\))\](\[[^\]]*\]|\([^)]*\))", " ", b)
            b = re.sub(r"!\[[^\]]*\](\([^)]*\)|\[[^\]]*\])", " ", b)
            b = re.sub(r"\[([^\]]+)\]\[[^\]]*\]", r"\1", b)
            b = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", b)
            b = re.sub(r"[*_`>|=]{2,}|-{3,}|^#+", " ", b, flags=re.M)
            b = " ".join(b.replace("#", " ").split())
            if len(b) > 50 and not re.match(r"(table of|build|license|install)", b.lower()):
                return b[:420]
    return ""


def _fichiers_python(racine, sous_dossier=None):
    base = os.path.join(racine, sous_dossier) if sous_dossier else racine
    for dossier, sous, fichiers in os.walk(base):
        sous[:] = sorted(s for s in sous if s.lower() not in DOSSIERS_IGNORES and not s.startswith("."))
        prof = os.path.relpath(dossier, racine).count(os.sep) + (0 if dossier == racine else 1)
        if prof > 5:
            continue
        for f in sorted(fichiers):
            if f.endswith(".py") and not f.startswith("test_") and f not in ("setup.py", "conftest.py"):
                yield os.path.normpath(os.path.join(dossier, f)), prof


def _outils_depuis_click(fichier_principal, res_principal, fichiers_paquet):
    """Construit les outils d'un programme click (groupe + sous-commandes éventuelles)."""
    groupes = [c for c in res_principal["click"] if c["groupe"]]
    if not groupes:
        cmds = [c for c in res_principal["click"] if not c["groupe"]]
        return [{"nom": "", "aide": c["aide"], "prefixe": [], "params": c["params"]} for c in cmds[:1]]
    groupe = groupes[0]
    communs = [dict(p, avant=True) for p in groupe["params"]]
    outils = []
    for f in fichiers_paquet:
        res = analyser_fichier(f)
        for c in (res or {}).get("click", []):
            if c["groupe"]:
                continue
            outils.append({"nom": c["nom"], "aide": c["aide"], "prefixe": [c["nom"]], "params": communs + c["params"]})
    return outils


def analyser_depot(racine, nom_depot=""):
    """Analyse un dépôt complet : que sait-il faire, avec quelles options, et comment le lancer."""
    readme = lire_readme(racine)
    scripts = scripts_declares(racine)
    prog = nom_depot.split("/")[-1] if nom_depot else ""

    # 1) Le projet déclare une commande officielle : c'est elle qu'on utilise.
    for nom_cmd, cible in scripts.items():
        module, fonction = cible.split(":")
        fichier, base = _module_vers_fichier(racine, module)
        if not fichier:
            continue
        res = analyser_fichier(fichier, nom_cmd) or {}
        paquet = os.path.join(racine, base, module.split(".")[0])
        fichiers_paquet = [f for f, _ in _fichiers_python(racine, os.path.relpath(paquet, racine))] \
            if os.path.isdir(paquet) else [fichier]
        outils = []
        if res.get("click"):
            outils = _outils_depuis_click(fichier, res, fichiers_paquet)
        elif res.get("outils"):
            outils = res["outils"]
        else:
            # La fonction lancée construit ses options dans un autre fichier du paquet
            meilleur = None
            for f in fichiers_paquet:
                r = analyser_fichier(f, nom_cmd)
                n = sum(len(o["params"]) for o in (r or {}).get("outils", []))
                if r and n and (not meilleur or n > meilleur[0]):
                    meilleur = (n, r)
            if meilleur:
                res, outils = meilleur[1], meilleur[1]["outils"]
        if outils:
            return _resultat(outils, res.get("description") or readme, res.get("style") or "click",
                             {"type": "script", "nom": nom_cmd, "module": module, "fonction": fonction,
                              "base": base or ""}, scripts)

    # 2) Sinon : le fichier Python qui se lance tout seul et qui a le plus d'options.
    candidats = []
    for chemin, prof in _fichiers_python(racine):
        res = analyser_fichier(chemin, prog)
        if not res or not (res["a_main"] or chemin.endswith("__main__.py")):
            continue
        outils = res["outils"] or ([{"nom": "", "aide": c["aide"], "prefixe": [], "params": c["params"]}
                                    for c in res["click"] if not c["groupe"]][:1])
        n = sum(len(o["params"]) for o in outils)
        if not n:
            continue
        nom_f = os.path.basename(chemin)
        score = n * 10 + (15 if nom_f in ("main.py", "cli.py", "__main__.py", "run.py", "app.py") else 0) - prof * 4
        candidats.append((score, chemin, res, outils))

    if not candidats:
        return {
            "ok": False, "description": readme,
            "raison": "Aucun outil utilisable trouvé. Ce projet ne semble pas avoir de commande Python avec "
                      "des options : c'est peut-être une bibliothèque pour développeurs, un site web, "
                      "ou un projet écrit dans un autre langage que Python.",
        }
    candidats.sort(key=lambda c: -c[0])
    _, chemin, res, outils = candidats[0]
    module = _module_python(racine, chemin)
    if module:
        lancement = {"type": "module", "module": module[0], "base": module[1]}
    else:
        lancement = {"type": "fichier", "fichier": os.path.relpath(chemin, racine).replace(os.sep, "/")}
    r = _resultat(outils, res["description"] or readme, res["style"], lancement, scripts)
    if len(candidats) > 1:
        r["avertissements"].append(f"{len(candidats)} programmes possibles trouvés dans ce projet ; "
                                   "le plus complet a été choisi.")
    return r


def _resultat(outils, description, style, lancement, scripts):
    # Les outils qui travaillent sur un fichier passent en premier : ce sont les plus utiles au grand public
    outils.sort(key=lambda o: (not any(p["genre"] == "fichier" and p["requis"] for p in o["params"]),
                               not any(p["genre"] in ("fichier", "dossier") for p in o["params"])))
    for o in outils:
        # positionnels d'abord (dans l'ordre d'écriture), puis les options ; les "communs" restent devant
        o["params"].sort(key=lambda p: (not p["avant"], not p["positionnel"]))
        o["libelle"] = _libelle(o["nom"]) if o["nom"] else "Utiliser l'outil"
    return {"ok": True, "description": description, "style": style, "outils": outils,
            "lancement": lancement, "avertissements": [], "scripts": scripts}


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(analyser_depot(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""),
                     indent=1, ensure_ascii=False))
