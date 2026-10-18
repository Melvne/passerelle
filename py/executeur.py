"""
Exécuteur : transforme ce que l'utilisateur a rempli dans l'interface en commande,
lance l'outil dans son espace séparé, et récupère le résultat.
"""

import os
import re
import shlex
import subprocess
import io

from installeur import WINDOWS

EXT_IMAGES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")


def nom_sur(nom, defaut="resultat"):
    """Nom de fichier sans risque (pas de dossier, pas de caractères bizarres)."""
    nom = os.path.basename((nom or "").replace("\\", "/")).strip()
    nom = re.sub(r"[^\w.\- ()]", "_", nom)
    return nom.strip(". ") or defaut


def _decouper(texte):
    try:
        if WINDOWS:
            return [p.strip('"') for p in shlex.split(texte, posix=False)]
        return shlex.split(texte)
    except ValueError:
        return texte.split()


def commande_de_base(lancement, dossier_code, python, paquet_installe=False):
    """
    Commande qui démarre l'outil, et le dossier à ajouter au chemin Python.
    Si le projet est installé comme un vrai paquet, on utilise cette version installée
    (elle contient ses fichiers de données) plutôt que le code brut.
    """
    base = "" if paquet_installe else os.path.join(dossier_code, lancement.get("base") or "")
    if lancement["type"] == "script":
        code = (
            "import sys, importlib\n"
            f"sys.argv[0] = {lancement['nom']!r}\n"
            f"f = importlib.import_module({lancement['module']!r})\n"
            f"for a in {lancement['fonction']!r}.split('.'): f = getattr(f, a)\n"
            "r = f()\n"
            "sys.exit(r if isinstance(r, int) else 0)\n"
        )
        return [python, "-c", code], base
    if lancement["type"] == "module":
        return [python, "-m", lancement["module"]], base
    return [python, os.path.join(dossier_code, lancement["fichier"])], dossier_code


def _extension_probable(param, fichiers_recus):
    if isinstance(param.get("defaut"), str) and os.path.splitext(param["defaut"])[1]:
        return os.path.splitext(param["defaut"])[1]
    for chemins in fichiers_recus.values():
        for c in chemins:
            ext = os.path.splitext(c)[1].lower()
            if ext:
                return ".png" if ext in EXT_IMAGES else ext
    return ".txt"


def construire_arguments(outil, valeurs, fichiers_recus, dossier_job):
    """
    valeurs        : {nom_du_param: texte saisi}  (cases cochées → "on")
    fichiers_recus : {nom_du_param: [chemins des fichiers envoyés]}
    Retourne la liste des arguments à donner à l'outil.
    """
    sorties = os.path.join(dossier_job, "sorties")
    avant_opt, avant_pos, opt, pos = [], [], [], []

    for p in outil["params"]:
        nom, genre = p["nom"], p["genre"]
        brut = (valeurs.get(nom) or "").strip()
        morceaux = []

        if genre == "case":
            coche = brut == "on"
            if (coche and not p["inverse"]) or (not coche and p["inverse"]):
                morceaux = [p["drapeau"]] if p["drapeau"] else []
        elif genre in ("fichier", "dossier"):
            chemins = fichiers_recus.get(nom, [])
            if genre == "dossier" and chemins:
                morceaux = [os.path.dirname(chemins[0])]
            elif chemins:
                morceaux = chemins if p["plusieurs"] else chemins[:1]
            elif brut:  # chemin ou lien écrit à la main
                morceaux = [brut]
        elif genre in ("sortie", "sortie_dossier"):
            if brut or p["requis"]:
                defaut = "resultats" if genre == "sortie_dossier" else "resultat" + _extension_probable(p, fichiers_recus)
                chemin = os.path.join(sorties, nom_sur(brut, defaut))
                if genre == "sortie_dossier":
                    os.makedirs(chemin, exist_ok=True)
                morceaux = [chemin]
        else:
            if brut and not (p["defaut"] is not None and brut == str(p["defaut"])):
                morceaux = _decouper(brut) if p["plusieurs"] else [brut]

        if not morceaux:
            continue
        if p["positionnel"]:
            (avant_pos if p["avant"] else pos).extend(morceaux)
        else:
            cible = avant_opt if p["avant"] else opt
            if genre == "case":
                cible.extend(morceaux)
            elif p["plusieurs"] and genre in ("fichier",):
                for m in morceaux:
                    cible.extend([p["drapeau"], m])
            else:
                cible.extend([p["drapeau"]] + morceaux)

    return avant_opt + avant_pos + list(outil.get("prefixe", [])) + opt + pos


def lancer(commande, dossier_python, dossier_job, journal, delai=3600):
    """Exécute l'outil. Le dossier de travail est le dossier « sorties » : tout ce qu'il crée y atterrit."""
    sorties = os.path.join(dossier_job, "sorties")
    os.makedirs(sorties, exist_ok=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", MPLBACKEND="Agg")
    if dossier_python:
        env["PYTHONPATH"] = dossier_python + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    options = {"creationflags": 0x08000000} if WINDOWS else {}
    proc = subprocess.Popen(commande, cwd=sorties, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env, **options)
    for ligne in io.TextIOWrapper(proc.stdout, encoding="utf-8", errors="replace"):
        journal(ligne.rstrip("\n"))
    try:
        return proc.wait(timeout=delai)
    except subprocess.TimeoutExpired:
        proc.kill()
        journal("L'outil a mis trop de temps et a été arrêté.")
        return -1


def lister_resultats(dossier_job):
    sorties = os.path.join(dossier_job, "sorties")
    resultats = []
    for dossier, _, fichiers in os.walk(sorties):
        for f in sorted(fichiers):
            chemin = os.path.join(dossier, f)
            rel = os.path.relpath(chemin, sorties).replace(os.sep, "/")
            ext = os.path.splitext(f)[1].lower()
            resultats.append({
                "nom": rel, "taille": os.path.getsize(chemin),
                "type": "image" if ext in EXT_IMAGES else ("texte" if ext in (".txt", ".md", ".csv", ".json", ".srt", ".vtt", ".log") else "autre"),
            })
    return resultats
