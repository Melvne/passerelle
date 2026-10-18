# Passerelle en ligne

Passerelle transforme un projet GitHub en application, **directement dans le navigateur**.
Python tourne dans la page (grâce à Pyodide) : rien à installer pour les visiteurs, et rien ne tourne sur un serveur.
C'est donc gratuit à héberger et sûr : chaque outil reste enfermé dans l'onglet du visiteur.

## Mettre le site en ligne gratuitement (GitHub Pages, environ 10 minutes)

1. **Créer un compte GitHub** (gratuit) sur https://github.com/signup si vous n'en avez pas.
2. **Créer un dépôt** : en haut à droite, « + » puis « New repository ».
   - Nom : `passerelle` (ou le nom de votre choix).
   - Cochez **Public**.
   - Cliquez sur « Create repository ».
3. **Envoyer les fichiers** : sur la page du nouveau dépôt, cliquez sur le lien « uploading an existing file ».
   - Décompressez d'abord le fichier ZIP de Passerelle sur votre ordinateur.
   - Ouvrez le dossier `passerelle-web`, sélectionnez **tout son contenu** (le fichier `index.html`, le fichier `travailleur.js`, le dossier `py`, etc.) et glissez-le dans la page.
   - Attention : il faut glisser le *contenu* du dossier, pas le dossier lui-même, pour que `index.html` soit à la racine.
   - En bas, cliquez sur « Commit changes ».
4. **Activer la publication** : onglet « Settings » du dépôt, puis « Pages » dans le menu de gauche.
   - Dans « Branch », choisissez `main` et le dossier `/ (root)`, puis « Save ».
5. **Attendre une à deux minutes**, puis rechargez la page « Pages » : l'adresse de votre site s'affiche en haut,
   de la forme `https://votre-nom.github.io/passerelle/`. C'est le lien à partager.

Pour mettre à jour le site plus tard : refaites l'étape 3 avec les nouveaux fichiers (ils remplacent les anciens).

Autre option sans GitHub : https://app.netlify.com/drop permet de glisser le dossier `passerelle-web` pour obtenir un lien (compte gratuit nécessaire pour le garder).

## Essayer sur votre ordinateur avant de publier

Un double-clic sur `index.html` **ne fonctionne pas** : les navigateurs interdisent à une page ouverte depuis un fichier de lancer Python en arrière-plan.
Sous Windows, double-cliquez plutôt sur `Essayer-en-local.bat` (Python doit être installé) : la page s'ouvre à l'adresse http://localhost:8000.

## L'intelligence artificielle (facultative)

Dans la page « Intelligence artificielle » du site, chaque visiteur peut coller sa propre clé d'accès à Claude
(obtenue sur https://console.anthropic.com). Passerelle envoie alors à l'IA le mode d'emploi et les fichiers de code
clés de chaque projet ; l'IA décrit comment lancer l'outil et quels champs afficher, avec des textes en français.
Sa réponse est vérifiée avant d'être utilisée (chemins existants, types de champs connus). Sans clé, ou si l'IA échoue,
Passerelle utilise son analyse automatique. La clé reste dans le navigateur du visiteur et n'est envoyée qu'à Claude.

## Ce qui fonctionne

- N'importe quel dépôt GitHub **public**.
- Les outils Python en ligne de commande (bibliothèques `argparse`, `optparse` et `click`), y compris ceux à plusieurs actions.
- Les dépendances écrites uniquement en Python, plus celles que Pyodide fournit déjà (numpy, pandas, Pillow, matplotlib, scipy…).
- Envoi de fichiers, cases à cocher, listes, nombres, textes ; résultats affichés (images, texte) et téléchargeables.
- La liste « Mes applications » est gardée dans le navigateur du visiteur.

Projets conseillés pour une démonstration : `lincolnloop/python-qrcode`, `pwaller/pyfiglet`, `Python-Markdown/markdown`.

## Limites (propres au navigateur)

- **Pas les outils lourds** : les projets qui dépendent de bibliothèques compilées non disponibles dans Pyodide
  (par exemple les outils d'IA comme `rembg`, `onnxruntime`, `torch`) ne s'installent pas. La version Windows reste là pour ceux-là.
- **Pas d'accès réseau depuis l'outil** : un outil qui télécharge des choses sur Internet pendant qu'il tourne échouera le plus souvent.
- **Pas de programmes externes** : un outil qui lance d'autres logiciels (ffmpeg, tesseract…) ne marchera pas.
- **Rechargement à chaque visite** : Python ne garde rien d'une visite à l'autre, l'outil est donc re-téléchargé et ré-installé
  à l'ouverture (quelques secondes pour un petit projet).
- **Limite GitHub** : sans compte, GitHub accepte environ 60 demandes par heure et par connexion ;
  chaque installation en utilise 2. C'est largement suffisant pour un usage normal.
- Les textes d'aide restent dans la langue du projet (souvent l'anglais).

## Organisation du code

| Fichier | Rôle |
|---|---|
| `index.html` | Interface (pages, formulaires générés, résultats) et mémoire du navigateur |
| `travailleur.js` | Tourne en arrière-plan : charge Pyodide, récupère le dépôt via l'API GitHub, orchestre analyse, installation et lancement |
| `py/moteur.py` | Côté Python : installation avec micropip, exécution de l'outil dans la page, capture de ce qu'il affiche et produit |
| `ia-consignes.js` | Consignes et format de réponse demandés à l'IA |
| `py/ia.py` | Prépare ce que l'IA doit lire et vérifie sa réponse |
| `py/analyseur.py` | Lit le code du dépôt sans l'exécuter et en déduit les actions et les options (le même que la version Windows) |
| `py/executeur.py`, `py/installeur.py` | Fonctions partagées avec la version Windows (construction des arguments, lecture des dépendances) |

Version de Pyodide utilisée : 0.28.3 (Python 3.13), chargée depuis cdn.jsdelivr.net.
