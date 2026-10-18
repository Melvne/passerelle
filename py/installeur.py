"""
Installeur : télécharge un dépôt GitHub (sans avoir besoin de Git), crée un environnement
Python séparé pour lui, et installe ses dépendances.
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

try:
    import tomllib
except ImportError:  # pragma: no cover
    tomllib = None

WINDOWS = os.name == "nt"
# Options d'installation « bonus » qu'on active quand un projet les propose (ex. rembg[cpu,cli])
EXTRAS_UTILES = ("cli", "cpu", "console", "scripts", "app", "tools", "pil", "png")


class ErreurInstallation(Exception):
    """Erreur avec un message compréhensible par tout le monde."""


# ------------------------------------------------------------------ lien GitHub

def lire_lien(texte):
    """'https://github.com/nom/projet' ou 'nom/projet' → ('nom', 'projet')."""
    t = (texte or "").strip()
    m = re.match(r"^(?:https?://)?(?:www\.)?github\.com/([\w.\-]+)/([\w.\-]+?)(?:\.git)?(?:[/?#].*)?$", t)
    if not m:
        m = re.match(r"^([\w.\-]+)/([\w.\-]+?)(?:\.git)?$", t)
    if not m:
        raise ErreurInstallation("Ce lien ne ressemble pas à un dépôt GitHub. "
                                 "Exemple attendu : https://github.com/nom/projet")
    return m.group(1), m.group(2)


def _requete(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Passerelle-POC",
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def infos_depot(proprietaire, depot):
    """Informations publiques sur le dépôt (branche principale, description)."""
    try:
        with _requete(f"https://api.github.com/repos/{proprietaire}/{depot}", 30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ErreurInstallation("Ce dépôt est introuvable : vérifiez le lien, ou il est peut-être privé.")
        return {}  # limite de requêtes atteinte, etc. : on continue sans ces infos
    except Exception:
        return {}


def telecharger(proprietaire, depot, destination, branche=None, journal=print):
    """Télécharge le code du dépôt en ZIP et le décompresse dans `destination`."""
    branches = [b for b in (branche, "main", "master") if b]
    derniere_erreur = None
    for b in dict.fromkeys(branches):
        url = f"https://codeload.github.com/{proprietaire}/{depot}/zip/refs/heads/{b}"
        journal(f"Téléchargement de {url}")
        try:
            with _requete(url, 300) as r:
                donnees = r.read()
            break
        except Exception as e:
            derniere_erreur = e
    else:
        raise ErreurInstallation(f"Impossible de télécharger le projet ({derniere_erreur}). "
                                 "Vérifiez votre connexion Internet.")
    journal(f"{len(donnees) // 1024} Ko reçus, décompression…")
    if os.path.exists(destination):
        supprimer(destination)
    os.makedirs(destination)
    with zipfile.ZipFile(io.BytesIO(donnees)) as z:
        for info in z.infolist():
            # Le ZIP contient un dossier « projet-main/… » : on retire ce premier niveau
            parties = info.filename.split("/", 1)
            if len(parties) < 2 or not parties[1]:
                continue
            cible = os.path.normpath(os.path.join(destination, parties[1]))
            if not cible.startswith(os.path.normpath(destination) + os.sep):
                continue  # protection contre les chemins piégés
            if info.is_dir():
                os.makedirs(cible, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(cible), exist_ok=True)
                with z.open(info) as src, open(cible, "wb") as dst:
                    shutil.copyfileobj(src, dst)


def supprimer(chemin):
    def _forcer(fonction, p, _):
        try:
            os.chmod(p, 0o700)
            fonction(p)
        except Exception:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(chemin, onexc=_forcer)
    else:
        shutil.rmtree(chemin, onerror=_forcer)


# ------------------------------------------------------------------ environnement séparé

def python_de(dossier_env):
    if WINDOWS:
        return os.path.join(dossier_env, "Scripts", "python.exe")
    return os.path.join(dossier_env, "bin", "python")


def executer(commande, journal, cwd=None):
    """Lance une commande et recopie ce qu'elle affiche dans le journal. Retourne le code de sortie."""
    options = {}
    if WINDOWS:
        options["creationflags"] = 0x08000000  # pas de fenêtre noire qui s'ouvre
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PIP_NO_INPUT="1")
    proc = subprocess.Popen(commande, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env, **options)
    for ligne in io.TextIOWrapper(proc.stdout, encoding="utf-8", errors="replace"):
        ligne = ligne.rstrip()
        if ligne:
            journal(ligne)
    return proc.wait()


def creer_environnement(dossier_env, journal=print):
    if os.path.exists(python_de(dossier_env)):
        journal("Espace séparé déjà présent.")
        return python_de(dossier_env)
    journal("Création d'un espace Python séparé pour ce projet…")
    code = executer([sys.executable, "-m", "venv", dossier_env], journal)
    if code != 0 or not os.path.exists(python_de(dossier_env)):
        raise ErreurInstallation("Impossible de créer l'espace séparé pour ce projet.")
    return python_de(dossier_env)


# ------------------------------------------------------------------ dépendances

def _lire_toml(racine):
    p = os.path.join(racine, "pyproject.toml")
    if not (tomllib and os.path.exists(p)):
        return {}
    try:
        with open(p, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def extras_disponibles(racine):
    data = _lire_toml(racine)
    noms = set((data.get("project", {}).get("optional-dependencies") or {}).keys())
    noms |= set((data.get("tool", {}).get("poetry", {}).get("extras") or {}).keys())
    for f in ("setup.py", "setup.cfg"):
        p = os.path.join(racine, f)
        if os.path.exists(p):
            texte = open(p, encoding="utf-8", errors="replace").read()
            bloc = re.search(r"extras_require\s*=\s*\{(.*?)\}", texte, re.S)
            if bloc:
                noms |= set(re.findall(r"""['"]([\w\-]+)['"]\s*:""", bloc.group(1)))
    return sorted(n for n in noms if n.lower() in EXTRAS_UTILES)


def _poetry_vers_pip(nom, spec):
    """Convertit une dépendance au format Poetry (« ^1.2 ») en format pip (« >=1.2 »)."""
    if isinstance(spec, dict):
        spec = spec.get("version", "*")
    if not isinstance(spec, str):
        spec = "*"
    spec = spec.strip()
    if spec in ("*", ""):
        return nom
    if spec[0] in "^~":
        return f"{nom}>={spec[1:].lstrip('=')}"
    if spec[0].isdigit():
        return f"{nom}=={spec}" if "*" not in spec else nom
    return f"{nom}{spec}"


def dependances_declarees(racine, extras):
    """Liste des dépendances écrites dans pyproject.toml (plan B si l'installation du projet échoue)."""
    data = _lire_toml(racine)
    deps = list(data.get("project", {}).get("dependencies") or [])
    opt = data.get("project", {}).get("optional-dependencies") or {}
    for e in extras:
        deps += opt.get(e, [])
    poetry = data.get("tool", {}).get("poetry", {})
    if poetry.get("dependencies"):
        choisis = set()
        for e in extras:
            choisis |= set(poetry.get("extras", {}).get(e, []))
        for nom, spec in poetry["dependencies"].items():
            if nom.lower() == "python":
                continue
            if isinstance(spec, dict) and spec.get("optional") and nom not in choisis:
                continue
            deps.append(_poetry_vers_pip(nom, spec))
    return deps


def installer_dependances(racine, python, journal=print):
    """
    Installe ce dont le projet a besoin.
    Retourne (problèmes rencontrés, le projet est-il installé comme un vrai paquet ?).
    """
    pip = [python, "-m", "pip", "install", "--disable-pip-version-check", "--no-input"]
    problemes = []
    deps_ok = False
    paquet_installe = False

    req = os.path.join(racine, "requirements.txt")
    if os.path.exists(req):
        journal("Installation des dépendances listées dans requirements.txt…")
        if executer(pip + ["-r", req], journal, cwd=racine) == 0:
            deps_ok = True
        else:
            problemes.append("Certaines dépendances de requirements.txt n'ont pas pu être installées.")

    a_paquet = any(os.path.exists(os.path.join(racine, f)) for f in ("pyproject.toml", "setup.py", "setup.cfg"))
    if a_paquet:
        extras = extras_disponibles(racine)
        cible = "." + (f"[{','.join(extras)}]" if extras else "")
        journal(f"Installation du projet lui-même ({cible})…")
        if executer(pip + [cible], journal, cwd=racine) == 0:
            paquet_installe = True
        else:
            journal("Nouvel essai avec les outils de construction déjà présents…")
            if executer(pip + ["--no-build-isolation", cible], journal, cwd=racine) == 0:
                paquet_installe = True
        if not paquet_installe:
            deps = dependances_declarees(racine, extras)
            if deps:
                journal("Le projet n'a pas pu être installé : installation directe de ses dépendances…")
                if executer(pip + deps, journal, cwd=racine) == 0:
                    deps_ok = True
                else:
                    problemes.append("Certaines dépendances du projet n'ont pas pu être installées.")
            elif not deps_ok:
                problemes.append("Le projet n'a pas pu être installé ; il sera lancé directement depuis son code.")

    if not a_paquet and not os.path.exists(req):
        journal("Aucune dépendance déclarée : le projet n'utilise peut-être que Python lui-même.")
    return problemes, paquet_installe
