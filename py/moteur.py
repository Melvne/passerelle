"""
Moteur de Passerelle en ligne : tourne dans le navigateur grâce à Pyodide.

- installer() : installe le projet et ses dépendances avec micropip (paquets Python purs ou fournis par Pyodide)
- executer()  : lance l'outil *dans* Python (pas de sous-processus dans un navigateur), capture ce qu'il affiche
                et ce qu'il crée.
Le téléchargement des fichiers depuis GitHub est fait côté JavaScript (travailleur.js).
"""

import contextlib
import importlib
import io
import json
import os
import re
import runpy
import sys
import traceback

import analyseur
import executeur
import installeur

APPS = "/apps"


def dossier_code(id_app):
    return f"{APPS}/{id_app}/code"


# ------------------------------------------------------------------ analyse

def analyser(id_app, depot):
    return json.dumps(analyseur.analyser_depot(dossier_code(id_app), depot), ensure_ascii=False)


def nom_paquet(racine):
    """Nom sous lequel le projet est publié (pyproject.toml, setup.cfg ou setup.py)."""
    data = installeur._lire_toml(racine)
    nom = data.get("project", {}).get("name") or data.get("tool", {}).get("poetry", {}).get("name")
    if nom:
        return nom
    for f, motif in (("setup.cfg", r"^\s*name\s*=\s*([\w.\-]+)"), ("setup.py", r"""\bname\s*=\s*['"]([\w.\-]+)['"]""")):
        p = os.path.join(racine, f)
        if os.path.exists(p):
            m = re.search(motif, open(p, encoding="utf-8", errors="replace").read(), re.M)
            if m:
                return m.group(1)
    return None


def _requirements(racine):
    p = os.path.join(racine, "requirements.txt")
    if not os.path.exists(p):
        return []
    deps = []
    for ligne in open(p, encoding="utf-8", errors="replace"):
        ligne = ligne.split(" #")[0].strip()
        if not ligne or ligne.startswith(("#", "-", "git+", "http")) or "://" in ligne:
            continue
        deps.append(ligne)
    return deps


def infos_installation(id_app):
    racine = dossier_code(id_app)
    return json.dumps({"nom": nom_paquet(racine), "extras": installeur.extras_disponibles(racine)})


# ------------------------------------------------------------------ installation

async def installer(id_app, pypi_ok, journal):
    """
    pypi_ok : le projet publié sur PyPI sous ce nom est bien celui du dépôt GitHub (vérifié côté JavaScript).
    Retourne {"problemes": [...], "paquet_installe": bool}.
    """
    import micropip

    racine = dossier_code(id_app)
    nom = nom_paquet(racine)
    extras = installeur.extras_disponibles(racine)
    problemes, paquet_installe = [], False

    async def essayer(cible):
        try:
            await micropip.install(cible)
            return True, ""
        except Exception as e:
            return False, str(e).strip().splitlines()[0][:300] if str(e).strip() else repr(e)

    if nom and pypi_ok:
        cibles = [nom + (f"[{','.join(extras)}]" if extras else "")] + ([nom] if extras else [])
        for cible in cibles:
            journal(f"Installation de « {cible} » depuis PyPI…")
            ok, err = await essayer(cible)
            if ok:
                paquet_installe = True
                journal("Installé.")
                break
            journal(f"Échec : {err}")

    if not paquet_installe:
        deps = list(dict.fromkeys(_requirements(racine) + installeur.dependances_declarees(racine, extras)))
        if deps:
            journal(f"Installation des dépendances une par une ({len(deps)})…")
        for d in deps:
            ok, err = await essayer(d)
            journal(("  ✓ " if ok else "  ✗ ") + d + ("" if ok else f" ({err})"))
            if not ok:
                problemes.append(d)
        if not deps:
            journal("Aucune dépendance déclarée.")

    return json.dumps({"problemes": problemes, "paquet_installe": paquet_installe}, ensure_ascii=False)


# ------------------------------------------------------------------ exécution

MAGIQUES = [(b"\x89PNG", ".png"), (b"\xff\xd8\xff", ".jpg"), (b"GIF8", ".gif"), (b"%PDF", ".pdf"),
            (b"RIFF", ".webp"), (b"PK\x03\x04", ".zip"), (b"<svg", ".svg"), (b"<?xml", ".svg")]


class _TamponBinaire(io.RawIOBase):
    """sys.stdout.buffer : garde les octets écrits pour décider à la fin si c'est du texte ou un fichier."""

    def __init__(self, flux):
        self.flux = flux
        self.octets = bytearray()

    def writable(self):
        return True

    def write(self, b):
        self.flux.vider()
        self.octets += bytes(b)
        return len(b)


class Flux(io.TextIOBase):
    """Remplace l'écran : chaque ligne affichée par l'outil est envoyée à l'interface."""

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, ecrire):
        self._ecrire = ecrire
        self._ligne = ""
        self.buffer = _TamponBinaire(self)

    def writable(self):
        return True

    def isatty(self):
        return False

    _fd = None

    def fileno(self):
        # Certains outils vérifient s'ils écrivent dans un terminal : on répond « non » (comme une redirection)
        if Flux._fd is None:
            try:
                Flux._fd = os.open(os.devnull, os.O_WRONLY)
            except OSError:
                Flux._fd = 1
        return Flux._fd

    def write(self, s):
        s = str(s).replace("\r\n", "\n")
        self._ligne += s
        while "\n" in self._ligne:
            l, self._ligne = self._ligne.split("\n", 1)
            self._ecrire(l)
        return len(s)

    def detach(self):
        # Certains outils (ex. pyfiglet) passent l'écran en mode binaire : on leur donne le tampon d'octets
        self.vider()
        return self.buffer

    def vider(self):
        if self._ligne:
            self._ecrire(self._ligne)
            self._ligne = ""

    def flush(self):
        pass


def _code_sortie(e):
    if e.code is None:
        return 0
    if isinstance(e.code, int):
        return e.code
    print(e.code, file=sys.stderr)
    return 1


def _fausses_metadonnees(id_app):
    """
    Projet lancé depuis son code (non installé) : beaucoup d'outils lisent leur propre numéro de version
    dans les informations d'installation. On en crée de minimales pour qu'ils ne plantent pas.
    """
    racine = dossier_code(id_app)
    nom = nom_paquet(racine)
    if not nom:
        return None
    data = installeur._lire_toml(racine)
    version = data.get("project", {}).get("version") or data.get("tool", {}).get("poetry", {}).get("version") or "0.0.0"
    dossier = f"{APPS}/{id_app}/meta"
    info = os.path.join(dossier, f"{re.sub(r'[-.]+', '_', nom)}-{version}.dist-info")
    os.makedirs(info, exist_ok=True)
    with open(os.path.join(info, "METADATA"), "w", encoding="utf-8") as f:
        f.write(f"Metadata-Version: 2.1\nName: {nom}\nVersion: {version}\n")
    return dossier


def executer(id_app, outil_json, valeurs_json, fichiers_json, dossier_job, lancement_json, paquet_installe, ecrire):
    """Lance un outil et retourne son code de sortie (0 = tout va bien)."""
    outil = json.loads(outil_json)
    valeurs = json.loads(valeurs_json)
    fichiers = json.loads(fichiers_json)
    lancement = json.loads(lancement_json)
    code_dir = dossier_code(id_app)
    sorties = os.path.join(dossier_job, "sorties")
    os.makedirs(sorties, exist_ok=True)

    args = executeur.construire_arguments(outil, valeurs, fichiers, dossier_job)
    ecrire(f"[passerelle] arguments : {json.dumps(args, ensure_ascii=False)}")

    base = "" if paquet_installe else os.path.join(code_dir, lancement.get("base") or "")
    if lancement["type"] == "fichier":
        base = os.path.dirname(os.path.join(code_dir, lancement["fichier"]))

    meta = _fausses_metadonnees(id_app) if not paquet_installe else None
    modules_avant = set(sys.modules)
    chemin_avant, argv_avant, dossier_avant = list(sys.path), list(sys.argv), os.getcwd()
    stdin_avant = sys.stdin
    flux = Flux(ecrire)
    code = 0
    if base:
        sys.path.insert(0, base)
        if lancement["type"] == "fichier":
            sys.path.insert(1, code_dir)
    if meta:
        sys.path.append(meta)
    os.chdir(sorties)
    sys.stdin = io.StringIO("")
    try:
        with contextlib.redirect_stdout(flux), contextlib.redirect_stderr(flux):
            try:
                if lancement["type"] == "script":
                    sys.argv = [lancement["nom"]] + args
                    f = importlib.import_module(lancement["module"])
                    for a in lancement["fonction"].split("."):
                        f = getattr(f, a)
                    r = f()
                    code = r if isinstance(r, int) else 0
                elif lancement["type"] == "module":
                    sys.argv = [lancement["module"]] + args
                    runpy.run_module(lancement["module"], run_name="__main__", alter_sys=True)
                else:
                    chemin = os.path.join(code_dir, lancement["fichier"])
                    sys.argv = [chemin] + args
                    runpy.run_path(chemin, run_name="__main__")
            except SystemExit as e:
                code = _code_sortie(e)
            except BaseException:
                traceback.print_exc()
                code = 1
        flux.vider()
        # Des octets bruts envoyés à l'écran : texte lisible, ou un fichier (ex. une image) à garder
        if flux.buffer.octets:
            donnees = bytes(flux.buffer.octets)
            try:
                for l in donnees.decode("utf-8").splitlines():
                    ecrire(l)
            except UnicodeDecodeError:
                ext = next((e for m, e in MAGIQUES if donnees.lstrip()[:len(m)] == m), ".bin")
                with open(os.path.join(sorties, "resultat" + ext), "wb") as f:
                    f.write(donnees)
                ecrire(f"[passerelle] l'outil a produit un fichier ({len(donnees)} octets), enregistré sous resultat{ext}")
    finally:
        sys.path[:] = chemin_avant
        sys.argv = argv_avant
        sys.stdin = stdin_avant
        os.chdir(dossier_avant)
        for nom in set(sys.modules) - modules_avant:
            sys.modules.pop(nom, None)   # repartir de zéro au prochain lancement
    return code


def resultats(dossier_job):
    return json.dumps(executeur.lister_resultats(dossier_job), ensure_ascii=False)
