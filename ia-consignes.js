/*
 * IA gratuite de Passerelle : un modèle open source (Qwen2.5-Coder 3B) qui tourne dans le navigateur
 * grâce à WebLLM et WebGPU. Aucune clé, aucun compte, aucun serveur : tout reste sur l'ordinateur du visiteur.
 */
"use strict";

const IA_WEBLLM = "https://cdn.jsdelivr.net/npm/@mlc-ai/web-llm@0.2.84/+esm";
const IA_MODELE_F16 = "Qwen2.5-Coder-3B-Instruct-q4f16_1-MLC";
const IA_MODELE_F32 = "Qwen2.5-Coder-3B-Instruct-q4f32_1-MLC";
const IA_CONTEXTE = 8192;     // nombre de « mots » (jetons) que le modèle peut lire et écrire en une fois
const IA_TAILLE_GO = "2";     // taille approximative du téléchargement

const CONSIGNES_IA = `You analyze a GitHub project for "Passerelle", which turns command-line Python tools into simple apps for non-technical French speakers. The tool runs in the browser (Pyodide): it only receives command-line arguments; files it writes in its working folder are offered for download; what it prints is shown.

Answer with JSON only:
- "utilisable": false if this is not a runnable command-line tool (library only, web server, GUI app, other language); then "raison" in simple French.
- "description": what the tool does, one simple French sentence.
- "lancement": how to start it, using files that really exist:
  {"type":"script","nom":<command>,"module":<python module like pkg.cli>,"fonction":<function like main>} for [project.scripts]/console_scripts entries,
  or {"type":"module","module":<module with __main__>},
  or {"type":"fichier","fichier":<path of a .py file>}.
- "outils": one item per action (one per sub-command, with "prefixe" = sub-command words; otherwise one item with "prefixe": []).
  Each item: "libelle" (short French), "aide" (French), "params": the useful arguments, each with:
  "nom" (identifier), "positionnel" (true if given without flag, in order), "drapeau" (real flag like "--output", "" if positional),
  "genre": one of "fichier" (input file), "dossier" (input folder), "sortie" (output file name), "sortie_dossier", "case" (flag without value), "choix" (fixed values, give "choix"), "entier", "nombre", "texte",
  "libelle" (2-5 French words), "aide" (short French), "requis", "defaut" (as text, "" if none), "choix" (list, [] if none), "inverse" (true for a flag that turns a feature off; describe the feature positively).
- Skip debug, verbose, version and server options. Never invent arguments that are not in the code.`;

// Format imposé à la réponse : le modèle ne peut produire que du JSON qui respecte cette forme
const SCHEMA_IA = {
  type: "object",
  properties: {
    utilisable: { type: "boolean" },
    raison: { type: "string" },
    description: { type: "string" },
    lancement: {
      type: "object",
      properties: {
        type: { type: "string", enum: ["script", "module", "fichier"] },
        nom: { type: "string" }, module: { type: "string" }, fonction: { type: "string" }, fichier: { type: "string" },
      },
      required: ["type"],
    },
    outils: {
      type: "array",
      items: {
        type: "object",
        properties: {
          libelle: { type: "string" }, aide: { type: "string" },
          prefixe: { type: "array", items: { type: "string" } },
          params: {
            type: "array",
            items: {
              type: "object",
              properties: {
                nom: { type: "string" }, positionnel: { type: "boolean" }, drapeau: { type: "string" },
                genre: { type: "string", enum: ["texte", "fichier", "dossier", "sortie", "sortie_dossier", "case", "choix", "entier", "nombre"] },
                libelle: { type: "string" }, aide: { type: "string" }, requis: { type: "boolean" },
                defaut: { type: "string" }, choix: { type: "array", items: { type: "string" } }, inverse: { type: "boolean" },
              },
              required: ["nom", "positionnel", "drapeau", "genre", "libelle"],
            },
          },
        },
        required: ["libelle", "params"],
      },
    },
  },
  required: ["utilisable"],
};
