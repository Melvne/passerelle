/*
 * Consignes et format de réponse demandés à l'IA (Claude) pour comprendre un projet.
 * Chargé par travailleur.js.
 */
"use strict";

const MODELE_IA = "claude-sonnet-5-5";

const CONSIGNES_IA = `You are the analysis engine of "Passerelle", a platform that turns any GitHub repository into a ready-to-use application with a simple graphical interface, for people with no technical skills (French-speaking audience).

You receive: the repository name and description, the list of retrieved files, an automatic rule-based analysis (may be incomplete or wrong), the README, configuration files and excerpts of the most relevant Python code.

Your job: decide how to run the tool and describe the form fields that the interface must show. The tool will be run inside the browser with Pyodide (Python 3.13, WebAssembly): no subprocesses, no network access from the tool, no compiled libraries other than those shipped with Pyodide (numpy, pandas, Pillow, scipy, matplotlib, lxml, etc. are fine; torch, onnxruntime, tensorflow, GUI toolkits, databases, audio devices are not). The tool receives its inputs only through command-line arguments (sys.argv); its working directory is a folder where any file it writes is collected and offered to the user for download; what it prints is shown to the user.

Rules:
- "lancement" must point to code that really exists in the file list:
  - type "script": a function to call, given by "module" (dotted Python module, e.g. "pkg.cli") and "fonction" (e.g. "main"), typically from [project.scripts] / console_scripts. "nom" is the command name.
  - type "module": run as "python -m module" (the module or package must have a __main__).
  - type "fichier": run a .py file by its path relative to the repository root.
- Describe every useful argument. "positionnel": true for arguments given without a flag, in the exact order the program expects them; otherwise give the real flag ("drapeau", e.g. "--output" or "-o").
- "genre" is the kind of form field: "fichier" (a file the user provides), "dossier" (several files placed in one folder), "sortie" (name of a file the tool will write), "sortie_dossier" (name of an output folder), "case" (checkbox flag without value), "choix" (fixed list of values, give "choix"), "entier", "nombre", "texte".
- For a flag that is ON by default and whose presence turns a feature OFF (argparse store_false), use "case" with "inverse": true and describe the feature positively.
- If the program has sub-commands, create one entry in "outils" per useful sub-command, with "prefixe" = the words to put on the command line (e.g. ["convert"]). Options that must come before the sub-command get "avant": true.
- Leave out arguments that make no sense for a beginner in a browser (verbosity, debug, version, server ports, cache paths) unless they are required.
- Write "description", "libelle", "aide" and "avertissements" in clear, simple French for non-technical people (no jargon, short sentences). "libelle" is a short field label (2 to 5 words).
- If the project cannot become a working application in the browser (it is a library without a command, a web app or server, a GUI app, a project in another language, or it needs a forbidden library), set "utilisable": false and explain why in "raison", in simple French.
- Never invent arguments that are not in the code or the README.`;

const OUTIL_IA = {
  name: "decrire_interface",
  description: "Décrit comment lancer l'outil et quels champs afficher dans l'interface.",
  input_schema: {
    type: "object",
    properties: {
      utilisable: { type: "boolean" },
      raison: { type: "string", description: "Si non utilisable : pourquoi, en français simple." },
      description: { type: "string", description: "Ce que fait l'outil, en une ou deux phrases en français simple." },
      lancement: {
        type: "object",
        properties: {
          type: { type: "string", enum: ["script", "module", "fichier"] },
          nom: { type: "string" }, module: { type: "string" }, fonction: { type: "string" },
          fichier: { type: "string" }, base: { type: "string", description: "Dossier à ajouter au chemin Python, ex. 'src' (souvent vide)." },
        },
        required: ["type"],
      },
      outils: {
        type: "array",
        items: {
          type: "object",
          properties: {
            nom: { type: "string" }, libelle: { type: "string" }, aide: { type: "string" },
            prefixe: { type: "array", items: { type: "string" } },
            params: {
              type: "array",
              items: {
                type: "object",
                properties: {
                  nom: { type: "string" }, drapeau: { type: ["string", "null"] }, positionnel: { type: "boolean" },
                  libelle: { type: "string" }, aide: { type: "string" },
                  genre: { type: "string", enum: ["texte", "fichier", "dossier", "sortie", "sortie_dossier", "case", "choix", "entier", "nombre"] },
                  inverse: { type: "boolean" }, defaut: { type: ["string", "number", "boolean", "null"] },
                  choix: { type: "array", items: { type: "string" } },
                  requis: { type: "boolean" }, plusieurs: { type: "boolean" }, avant: { type: "boolean" },
                },
                required: ["nom", "positionnel", "genre", "libelle"],
              },
            },
          },
          required: ["libelle", "params"],
        },
      },
      avertissements: { type: "array", items: { type: "string" } },
    },
    required: ["utilisable"],
  },
};
