"""
Analyseur des projets JavaScript (Node.js) : lit le code sans l'exécuter et devine quelles options
l'outil accepte, pour générer une interface. Même format de résultat que analyseur.py.

Reconnaît : commander / cac (.option('-o, --output <file>', …)), yargs (.option('nom', {…}), .options({…})),
meow (flags: {…}) et la lecture « à la main » de process.argv (case '--output': …).
Le point de départ est le champ « bin » de package.json, puis on suit les fichiers importés.
"""

import json
import os
import re

from analyseur import _deviner_genre, _libelle, _nettoyer, _param, lire_readme

EXTENSIONS = (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts")


# ------------------------------------------------------------------ petit lecteur de valeurs JavaScript

def _fin_chaine(s, i):
    q = s[i]
    i += 1
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == q:
            return i + 1
        i += 1
    return len(s)


def _sans_commentaires(s):
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c in "'\"`":
            j = _fin_chaine(s, i)
            out.append(s[i:j])
            i = j
        elif s.startswith("//", i):
            j = s.find("\n", i)
            i = len(s) if j < 0 else j
        elif s.startswith("/*", i):
            j = s.find("*/", i + 2)
            i = len(s) if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _arguments_appel(s, debut):
    """s[debut] est juste après « ( » : retourne (liste des arguments en texte, position après « ) »)."""
    args, prof, i, dep = [], 0, debut, debut
    while i < len(s):
        c = s[i]
        if c in "'\"`":
            i = _fin_chaine(s, i)
            continue
        if c in "([{":
            prof += 1
        elif c in ")]}":
            if prof == 0:
                if s[dep:i].strip():
                    args.append(s[dep:i].strip())
                return args, i + 1
            prof -= 1
        elif c == "," and prof == 0:
            args.append(s[dep:i].strip())
            dep = i + 1
        i += 1
    return args, len(s)


def _valeur(t):
    """Valeur d'un petit morceau de JavaScript (texte, nombre, booléen, liste, objet), sinon None."""
    t = (t or "").strip()
    if not t:
        return None
    if t[0] in "'\"" and _fin_chaine(t, 0) == len(t):
        try:
            return json.loads('"' + t[1:-1].replace('"', '\\"').replace("\\'", "'") + '"')
        except ValueError:
            return t[1:-1]
    if t[0] == "`" and t.endswith("`") and "${" not in t:
        return t[1:-1]
    if re.fullmatch(r"-?\d+(\.\d+)?", t):
        return float(t) if "." in t else int(t)
    if t in ("true", "false"):
        return t == "true"
    if t in ("null", "undefined"):
        return None
    if t[0] == "[" and t.endswith("]"):
        elements, _ = _arguments_appel(t, 1)
        return [_valeur(e) for e in elements]
    if t[0] == "{" and t.endswith("}"):
        return _objet(t)
    return {"__code__": t}


def _objet(t):
    elements, _ = _arguments_appel(t, 1)
    obj = {}
    for e in elements:
        m = re.match(r"""^\s*(?:(['"])(.+?)\1|([\w$]+))\s*:\s*(.*)$""", e, re.S)
        if m:
            obj[m.group(2) or m.group(3)] = _valeur(m.group(4))
        elif re.fullmatch(r"[\w$]+", e.strip()):
            obj[e.strip()] = {"__code__": e.strip()}
    return obj


def _texte(v):
    return v if isinstance(v, str) else ""


# ------------------------------------------------------------------ fichiers

def lire_package(racine):
    try:
        with open(os.path.join(racine, "package.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _resoudre(racine, depuis, chemin):
    """Trouve le fichier source réel d'un import relatif (ou d'un bin qui pointe vers du code compilé)."""
    base = os.path.normpath(os.path.join(os.path.dirname(depuis), chemin)) if depuis else os.path.normpath(os.path.join(racine, chemin))
    if not base.startswith(os.path.normpath(racine)):
        return None
    candidats = [base] + [base + e for e in EXTENSIONS] + [os.path.join(base, "index" + e) for e in EXTENSIONS]
    sans_ext = re.sub(r"\.(m|c)?js$", "", base)
    candidats += [sans_ext + e for e in EXTENSIONS]
    # bin vers dist/ ou lib/ (code compilé absent du dépôt) : chercher l'équivalent dans src/
    rel = os.path.relpath(sans_ext, racine)
    for compile in ("dist/", "build/", "lib/", "out/"):
        if rel.startswith(compile):
            src = os.path.join(racine, "src", rel[len(compile):])
            candidats += [src + e for e in EXTENSIONS] + [os.path.join(src, "index" + e) for e in EXTENSIONS]
    for c in candidats:
        if os.path.isfile(c):
            return c
    return None


def _sources(racine, entree, limite=12):
    """Le fichier d'entrée et les fichiers locaux qu'il importe (sur 3 niveaux)."""
    vus, a_faire, textes = set(), [(entree, 0)], []
    while a_faire and len(textes) < limite:
        f, prof = a_faire.pop(0)
        if f in vus:
            continue
        vus.add(f)
        try:
            with open(f, encoding="utf-8", errors="replace") as h:
                s = h.read(400000)
        except OSError:
            continue
        textes.append((f, _sans_commentaires(s)))
        if prof >= 3:
            continue
        for m in re.finditer(r"""(?:from\s+|require\(\s*|import\(\s*)['"](\.{1,2}/[^'"]+)['"]""", s):
            r = _resoudre(racine, f, m.group(1))
            if r and r not in vus:
                a_faire.append((r, prof + 1))
    return textes


# ------------------------------------------------------------------ reconnaissance des options

DRAPEAUX = re.compile(r"^\s*(?:(-\w)\s*[,| ]\s*)?(--?[\w][\w.\-]*)?\s*(?:[ =]?([<\[][^>\]]+[>\]]))?")


def _depuis_drapeaux(flags, aide, defaut, requis=False, choix=None):
    """« -o, --output <file> » → un paramètre de formulaire."""
    m = DRAPEAUX.match(flags or "")
    if not m or not (m.group(1) or m.group(2)):
        return None
    court, long, valeur = m.group(1), m.group(2), m.group(3)
    drapeau = long or court
    if drapeau in ("-h", "--help", "-V", "--version", "-v") and not valeur:
        return None
    nom = drapeau.lstrip("-")
    inverse = False
    if nom.startswith("no-") and not valeur:
        nom, inverse = nom[3:], True
    nom = re.sub(r"\W", "_", nom)
    if not valeur:
        genre = "case"
    else:
        indice = re.sub(r"[<>\[\].]", "", valeur)
        genre = _deviner_genre(f"{nom} {indice}" if indice.lower() in ("file", "path", "dir", "folder", "directory", "output", "input") else nom,
                               aide, "", "", choix, defaut)
    p = _param(nom, drapeau, False, _nettoyer(aide), genre, defaut if valeur else None,
               [str(c) for c in choix] if choix else None, requis, bool(valeur and "..." in valeur), inverse=inverse)
    return p


def _depuis_argument(spec, aide):
    """« <input> » ou « [files...] » (argument sans drapeau) → un paramètre."""
    m = re.match(r"^\s*([<\[])\s*([\w\-]+)\s*(\.\.\.)?\s*[>\]]", spec or "")
    if not m:
        return None
    nom = re.sub(r"\W", "_", m.group(2))
    genre = _deviner_genre(nom, aide, "", "", None, None)
    return _param(nom, None, True, _nettoyer(aide), genre, None, None, m.group(1) == "<", bool(m.group(3)))


def _commander(s):
    """commander / cac : options et arguments, rangés par sous-commande."""
    commandes = [{"nom": "", "aide": "", "params": []}]
    for m in re.finditer(r"\.(command|option|requiredOption|argument|arguments|addOption|description)\s*\(", s):
        args, _ = _arguments_appel(s, m.end())
        quoi = m.group(1)
        v = [_valeur(a) for a in args]
        courant = commandes[-1]
        if quoi == "command" and isinstance(v[0] if v else None, str):
            mots = v[0].split()
            if not mots or mots[0].startswith(("<", "[")):
                continue
            nouvelle = {"nom": mots[0], "aide": _texte(v[1]) if len(v) > 1 else "", "params": []}
            for spec in mots[1:]:
                p = _depuis_argument(spec, "")
                if p:
                    nouvelle["params"].append(p)
            commandes.append(nouvelle)
        elif quoi == "description" and isinstance(v[0] if v else None, str) and not courant["aide"]:
            courant["aide"] = v[0]
        elif quoi in ("option", "requiredOption") and isinstance(v[0] if v else None, str) and v[0].lstrip().startswith("-"):
            defaut = v[2] if len(v) > 2 and isinstance(v[2], (str, int, float, bool)) else None
            p = _depuis_drapeaux(v[0], _texte(v[1]) if len(v) > 1 else "", defaut, quoi == "requiredOption")
            if p:
                courant["params"].append(p)
        elif quoi in ("argument", "arguments") and isinstance(v[0] if v else None, str):
            for spec in re.findall(r"[<\[][^>\]]+[>\]]", v[0]):
                p = _depuis_argument(spec, _texte(v[1]) if len(v) > 1 and quoi == "argument" else "")
                if p:
                    courant["params"].append(p)
        elif quoi == "addOption" and args:
            o = re.match(r"\s*new\s+Option\s*\(", args[0])
            if o:
                oargs, fin = _arguments_appel(args[0], o.end())
                ov = [_valeur(a) for a in oargs]
                reste = args[0][fin:]
                choix = None
                c = re.search(r"\.choices\s*\(", reste)
                if c:
                    cargs, _ = _arguments_appel(reste, c.end())
                    choix = _valeur(cargs[0]) if cargs else None
                    choix = choix if isinstance(choix, list) else None
                d = re.search(r"\.default\s*\(", reste)
                defaut = None
                if d:
                    dargs, _ = _arguments_appel(reste, d.end())
                    defaut = _valeur(dargs[0]) if dargs else None
                if isinstance(ov[0] if ov else None, str):
                    p = _depuis_drapeaux(ov[0], _texte(ov[1]) if len(ov) > 1 else "",
                                         defaut if isinstance(defaut, (str, int, float, bool)) else None,
                                         bool(re.search(r"\.makeOptionMandatory\s*\(", reste)), choix)
                    if p:
                        courant["params"].append(p)
    return commandes


def _yargs_option(nom, conf, descriptions, booleens, alias):
    conf = conf if isinstance(conf, dict) else {}
    if nom in ("help", "h", "version", "V", "$0", "_"):
        return None
    alias_conf = conf.get("alias")
    noms = [nom] + ([alias_conf] if isinstance(alias_conf, str) else [a for a in alias_conf or [] if isinstance(a, str)])
    noms += alias.get(nom, [])
    long = next((n for n in noms if len(n) > 1), nom)
    drapeau = ("--" if len(long) > 1 else "-") + long
    aide = _texte(conf.get("describe") or conf.get("description") or conf.get("desc")) or descriptions.get(nom, "")
    type_ = _texte(conf.get("type")) or ("boolean" if nom in booleens or conf.get("boolean") is True else "")
    choix = conf.get("choices") if isinstance(conf.get("choices"), list) else None
    defaut = conf.get("default") if isinstance(conf.get("default"), (str, int, float, bool)) else None
    if type_ == "boolean" or isinstance(defaut, bool):
        genre = "case"
    elif type_ == "number":
        genre = "nombre" if isinstance(defaut, float) else "entier"
    else:
        genre = _deviner_genre(long, aide, "", "", choix, defaut)
    requis = bool(conf.get("demandOption") or conf.get("demand") or conf.get("required"))
    return _param(re.sub(r"\W", "_", long), drapeau, False, _nettoyer(aide), genre,
                  None if genre == "case" else defaut, [str(c) for c in choix] if choix else None,
                  requis, conf.get("array") is True or type_ == "array")


def _yargs(s):
    if not re.search(r"\byargs\b|\.argv\b|\.parse\(\)", s):
        return []
    descriptions, booleens, alias, options = {}, set(), {}, []
    for m in re.finditer(r"\.(describe|boolean|alias)\s*\(", s):
        args, _ = _arguments_appel(s, m.end())
        v = [_valeur(a) for a in args]
        if m.group(1) == "describe":
            if v and isinstance(v[0], dict):
                descriptions.update({k: x for k, x in v[0].items() if isinstance(x, str)})
            elif len(v) > 1 and isinstance(v[0], str) and isinstance(v[1], str):
                descriptions[v[0]] = v[1]
        elif m.group(1) == "boolean":
            booleens.update(x for x in (v[0] if v and isinstance(v[0], list) else v) if isinstance(x, str))
        elif m.group(1) == "alias" and len(v) > 1 and isinstance(v[0], str):
            autres = v[1] if isinstance(v[1], list) else [v[1]]
            for cle in [v[0]] + [a for a in autres if isinstance(a, str)]:
                alias.setdefault(cle, []).extend(a for a in [v[0]] + autres if isinstance(a, str) and a != cle)
    for m in re.finditer(r"\.(option|options|positional)\s*\(", s):
        args, _ = _arguments_appel(s, m.end())
        v = [_valeur(a) for a in args]
        if m.group(1) == "options" and v and isinstance(v[0], dict):
            options += [(k, c, False) for k, c in v[0].items()]
        elif v and isinstance(v[0], str) and not v[0].lstrip().startswith("-"):
            options.append((v[0], v[1] if len(v) > 1 else {}, m.group(1) == "positional"))
    params, vus = [], set()
    for nom, conf, positionnel in options:
        if positionnel:
            conf = conf if isinstance(conf, dict) else {}
            aide = _texte(conf.get("describe") or conf.get("description"))
            p = _param(re.sub(r"\W", "_", nom), None, True, _nettoyer(aide), _deviner_genre(nom, aide, "", "", None, None),
                       None, None, False, conf.get("array") is True)
        else:
            p = _yargs_option(nom, conf, descriptions, booleens, alias)
        if p and p["nom"] not in vus:
            vus.add(p["nom"])
            params.append(p)
    # Options seulement décrites (.describe + .boolean), sans .option()
    for nom, aide in descriptions.items():
        if nom not in vus and nom not in ("h", "help", "version") and not any(nom in alias.get(p["nom"], []) for p in params):
            p = _yargs_option(nom, {}, descriptions, booleens, alias)
            if p and p["nom"] not in vus:
                vus.add(p["nom"])
                params.append(p)
    if params and re.search(r"argv\._|\$0 .*(text|message|input|file|<)", s):
        params.insert(0, _param("arguments", None, True, "Ce qu'on écrit après les options.", "texte", None, None, False, False))
        params[0]["libelle"] = "Texte à traiter"
    return params


def _meow(s):
    m = re.search(r"\bmeow\s*\(", s)
    if not m:
        return []
    args, _ = _arguments_appel(s, m.end())
    conf = next((_valeur(a) for a in args if a.lstrip().startswith("{")), None)
    aide = next((_valeur(a) for a in args if a.lstrip()[:1] in "`'\""), "")
    flags = conf.get("flags") if isinstance(conf, dict) else None
    params = []
    if isinstance(aide, str) and re.search(r"\$ \S+ .*<[\w\- ]+>", aide):
        indice = re.search(r"\$ \S+ .*?<([\w\- ]+)>", aide).group(1)
        params.append(_param(re.sub(r"\W", "_", indice), None, True, "", _deviner_genre(indice, "", "", "", None, None), None, None, True, False))
    for nom, f in (flags or {}).items():
        f = f if isinstance(f, dict) else {}
        kebab = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", nom).lower()
        type_ = _texte(f.get("type"))
        defaut = f.get("default") if isinstance(f.get("default"), (str, int, float, bool)) else None
        choix = f.get("choices") if isinstance(f.get("choices"), list) else None
        genre = "case" if type_ == "boolean" else ("nombre" if type_ == "number" else _deviner_genre(kebab, "", "", "", choix, defaut))
        aide_flag = ""
        if isinstance(aide, str):
            l = re.search(r"--" + re.escape(kebab) + r"[^\n]*?\s{2,}(\S[^\n]*)", aide)
            aide_flag = l.group(1) if l else ""
        params.append(_param(re.sub(r"\W", "_", kebab), "--" + kebab, False, _nettoyer(aide_flag), genre,
                             None if genre == "case" else defaut, [str(c) for c in choix] if choix else None,
                             bool(f.get("isRequired") is True), bool(f.get("isMultiple"))))
    return params


def _argv_manuel(s):
    """Programmes qui lisent process.argv eux-mêmes : case '-o': case '--output': output = argv.shift()."""
    if "argv" not in s:
        return []
    params, vus = [], set()
    for m in re.finditer(r"((?:case\s+['\"]-{1,2}[\w\-]+['\"]\s*:\s*)+)([^;]{0,160})", s):
        drapeaux = re.findall(r"['\"](-{1,2}[\w\-]+)['\"]", m.group(1))
        long = next((d for d in drapeaux if d.startswith("--")), drapeaux[0])
        if long in ("--help", "-h", "--version", "-v", "-V"):
            continue
        avec_valeur = bool(re.search(r"\.shift\(\)|\[\+\+\w+\]|\[\w+\s*\+\s*1\]", m.group(2)))
        nom = re.sub(r"\W", "_", long.lstrip("-"))
        if nom in vus:
            continue
        vus.add(nom)
        genre = _deviner_genre(nom, "", "", "", None, None) if avec_valeur else "case"
        params.append(_param(nom, long, False, "", genre, None, None, False, False))
    if params:
        params.insert(0, _param("arguments", None, True, "Ce qu'on écrit après les options.", "texte", None, None, False, False))
        params[0]["libelle"] = "Texte à traiter"
    return params


# ------------------------------------------------------------------ analyse du dépôt

def bins(pkg):
    b = pkg.get("bin")
    if isinstance(b, str):
        nom = (pkg.get("name") or "outil").split("/")[-1]
        return {nom: b}
    return {k: v for k, v in (b or {}).items() if isinstance(v, str)} if isinstance(b, dict) else {}


def analyser_depot(racine, nom_depot=""):
    pkg = lire_package(racine)
    readme = lire_readme(racine)
    description = _texte(pkg.get("description")) or readme
    liste = bins(pkg)
    entree, nom_bin, chemin_bin = None, None, None
    # Le bin qui porte le nom du dépôt ou du paquet en priorité
    nom_depot_court = nom_depot.split("/")[-1].lower() if nom_depot else ""
    for nom, chemin in sorted(liste.items(), key=lambda kv: (kv[0].lower() not in (nom_depot_court, (pkg.get("name") or "").split("/")[-1].lower()), kv[0])):
        f = _resoudre(racine, None, chemin)
        nom_bin, chemin_bin = nom, chemin
        if f:
            entree = f
            break
    if not liste:
        # Pas de « bin » : un fichier .js à la racine qui commence par #!/usr/bin/env node
        for n in sorted(os.listdir(racine)):
            p = os.path.join(racine, n)
            if n.endswith((".js", ".mjs", ".cjs")) and os.path.isfile(p):
                with open(p, encoding="utf-8", errors="replace") as h:
                    premiere = h.readline()
                if premiere.startswith("#!") and "node" in premiere:
                    entree, nom_bin, chemin_bin = p, n.rsplit(".", 1)[0], n
                    break
    if not nom_bin:
        return {"ok": False, "langage": "javascript", "description": description,
                "raison": "Ce projet JavaScript ne déclare aucune commande à lancer : c'est probablement une "
                          "bibliothèque pour développeurs, un site web ou une application qui a déjà sa propre interface."}

    textes = _sources(racine, entree) if entree else []
    code = "\n".join(t for _, t in textes)
    style, outils = None, []
    commandes = _commander(code) if re.search(r"commander|\bcac\b|program\s*\.|\.option\s*\(\s*['\"`]-", code) else []
    if commandes and sum(len(c["params"]) for c in commandes):
        style = "commander"
        communs = [dict(p, avant=True) for p in commandes[0]["params"]]
        sous = [c for c in commandes[1:] if c["nom"] not in ("help",)]
        if sous:
            outils = [{"nom": c["nom"], "aide": _nettoyer(c["aide"]), "prefixe": [c["nom"]], "params": communs + c["params"]} for c in sous]
        else:
            outils = [{"nom": "", "aide": _nettoyer(commandes[0]["aide"]), "prefixe": [], "params": commandes[0]["params"]}]
    if not outils:
        for nom_style, fonction in (("yargs", _yargs), ("meow", _meow), ("argv", _argv_manuel)):
            params = fonction(code)
            if params:
                style, outils = nom_style, [{"nom": "", "aide": "", "prefixe": [], "params": params}]
                break
    avertissements = []
    if not outils:
        # On sait lancer l'outil mais pas lire ses options : un champ libre, comme sur une ligne de commande
        style = "inconnu"
        p = _param("arguments", None, True, "Écrivez ce que vous taperiez après le nom de la commande.", "texte", None, None, False, True)
        p["libelle"] = "Arguments"
        outils = [{"nom": "", "aide": "", "prefixe": [], "params": [p]}]
        avertissements.append("Passerelle n'a pas pu lire les options de cet outil : un seul champ libre est proposé.")
    if not entree:
        avertissements.append("Le code de la commande n'est pas dans le dépôt (il est fabriqué à la publication) : "
                              "Passerelle utilisera la version publiée sur npm.")
    for o in outils:
        o["params"].sort(key=lambda p: (not p["avant"], not p["positionnel"]))
        o["libelle"] = _libelle(o["nom"]) if o["nom"] else "Utiliser l'outil"
    return {
        "ok": True, "langage": "javascript", "description": description, "style": style, "outils": outils,
        "lancement": {"type": "node", "paquet": pkg.get("name") or "", "bin": nom_bin, "fichier": re.sub(r"^\./", "", chemin_bin or ""),
                      "source_presente": bool(entree)},
        "avertissements": avertissements, "scripts": {},
    }


if __name__ == "__main__":
    import sys
    print(json.dumps(analyser_depot(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""), indent=1, ensure_ascii=False))
