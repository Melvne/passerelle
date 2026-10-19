/*
 * Travailleur de Passerelle en ligne.
 * Tourne en arrière-plan de la page : charge Python (Pyodide), récupère les fichiers d'un dépôt GitHub,
 * l'analyse, l'installe et lance l'outil, sans bloquer l'interface.
 */
"use strict";

const VERSION_PYODIDE = "0.28.3";
importScripts(`https://cdn.jsdelivr.net/pyodide/v${VERSION_PYODIDE}/full/pyodide.js`);

const VERSION_MOTEUR = "12";
const FICHIERS_MOTEUR = ["analyseur.py", "analyseur_js.py", "installeur.py", "executeur.py", "ia.py", "moteur.py"];
const DOSSIERS_IGNORES = new Set([".git", "venv", ".venv", "env", "node_modules", "__pycache__", "build", "dist",
  "site-packages", ".tox", "docs", "doc", "examples", "example", "tests", "test", "benchmarks", ".github"]);
const LIMITES = { fichiersLegers: 800, fichiersTotal: 2500, octetsTotal: 80 * 1024 * 1024, octetsFichier: 5 * 1024 * 1024 };

// Bibliothèques qui ne peuvent pas tourner dans un navigateur : si un projet en dépend, inutile de proposer l'outil
const LOURDS = /^(onnxruntime|torch|torchvision|torchaudio|tensorflow|jax|jaxlib|pyqt|pyside|playwright|selenium|psycopg|mysqlclient|pyaudio|sounddevice|pygame|keras|transformers|llama|ultralytics|gradio)/i;

let pyodide = null;
let moteur = null;
let chargement = null;
const prets = new Set();          // applications prêtes dans ce travailleur
const paquetsInstalles = new Map(); // id → résultat d'installation

class ErreurClaire extends Error {}
const envoyer = (m, transfert) => postMessage(m, transfert || []);

// ---------------------------------------------------------------- Python
function initialiser() {
  if (!chargement) {
    chargement = (async () => {
      envoyer({ type: "python", etat: "chargement" });
      pyodide = await loadPyodide();
      await pyodide.loadPackage("micropip");
      pyodide.FS.mkdirTree("/moteur");
      pyodide.FS.mkdirTree("/apps");
      for (const f of FICHIERS_MOTEUR) {
        const r = await fetch(`py/${f}?v=${VERSION_MOTEUR}`);
        if (!r.ok) throw new ErreurClaire(`Fichier du moteur manquant : py/${f}`);
        pyodide.FS.writeFile(`/moteur/${f}`, await r.text());
      }
      pyodide.runPython("import sys\nsys.path.insert(0, '/moteur')\nimport moteur");
      moteur = pyodide.globals.get("moteur");
      envoyer({ type: "python", etat: "pret" });
    })().catch((e) => {
      chargement = null;
      envoyer({ type: "python", etat: "erreur", message: String(e && e.message || e) });
      throw e;
    });
  }
  return chargement;
}

// ---------------------------------------------------------------- GitHub
async function github(url) {
  let r;
  try { r = await fetch(url, { headers: { Accept: "application/vnd.github+json" } }); }
  catch (e) { throw new ErreurClaire("Impossible de joindre GitHub. Vérifiez votre connexion Internet."); }
  if (r.status === 404) throw new ErreurClaire("Ce dépôt est introuvable : vérifiez le lien, ou il est peut-être privé.");
  if (r.status === 403 || r.status === 429) throw new ErreurClaire("GitHub limite le nombre de demandes (environ 60 par heure). Réessayez dans un moment.");
  if (!r.ok) throw new ErreurClaire(`GitHub a répondu avec une erreur (${r.status}).`);
  return r.json();
}

function ignore(chemin) {
  const parties = chemin.split("/");
  if (parties.length > 7) return true;
  return parties.slice(0, -1).some((p) => DOSSIERS_IGNORES.has(p.toLowerCase()) || p.startsWith("."));
}

function estLeger(chemin) {
  const nom = chemin.split("/").pop().toLowerCase();
  const racine = !chemin.includes("/");
  if (nom.endsWith(".py")) return true;
  if (/\.(m|c)?(js|ts)$/.test(nom) && !/\.(d\.ts|min\.js)$/.test(nom) && !/(^|\/)(test|spec|__tests__)\//.test(chemin)) return true;
  if (["pyproject.toml", "setup.py", "setup.cfg"].includes(nom) || (racine && nom === "package.json")) return true;
  if (racine && (nom.startsWith("readme") || /^requirements.*\.txt$/.test(nom))) return true;
  return false;
}

async function lireDepot(depot) {
  const info = await github(`https://api.github.com/repos/${depot}`);
  const branche = info.default_branch || "main";
  const arbre = await github(`https://api.github.com/repos/${depot}/git/trees/${encodeURIComponent(branche)}?recursive=1`);
  const fichiers = (arbre.tree || [])
    .filter((t) => t.type === "blob" && !ignore(t.path) && (t.size || 0) <= LIMITES.octetsFichier)
    .map((t) => [t.path, t.size || 0])
    .slice(0, LIMITES.fichiersTotal);
  return {
    depot: info.full_name || depot, branche, description: info.description || "",
    langage: info.language || "", tronque: !!arbre.truncated, fichiers,
  };
}

async function telechargerFichiers(id, meta, chemins, journal) {
  const base = `/apps/${id}/code/`;
  let suivant = 0, faits = 0, octets = 0;
  const erreurs = [];
  async function ouvrier() {
    while (suivant < chemins.length) {
      const chemin = chemins[suivant++];
      const url = `https://raw.githubusercontent.com/${meta.depot}/${encodeURIComponent(meta.branche)}/${chemin.split("/").map(encodeURIComponent).join("/")}`;
      try {
        const r = await fetch(url);
        if (!r.ok) throw new Error(r.status);
        const donnees = new Uint8Array(await r.arrayBuffer());
        octets += donnees.length;
        const dossier = (base + chemin).split("/").slice(0, -1).join("/");
        pyodide.FS.mkdirTree(dossier);
        pyodide.FS.writeFile(base + chemin, donnees);
      } catch (e) { erreurs.push(chemin); }
      faits++;
      if (faits % 25 === 0 || faits === chemins.length) journal(`  ${faits}/${chemins.length} fichiers récupérés`);
    }
  }
  await Promise.all(Array.from({ length: 12 }, ouvrier));
  if (erreurs.length) journal(`  ${erreurs.length} fichier(s) n'ont pas pu être récupérés.`);
  return octets;
}

// ---------------------------------------------------------------- IA (dans la page)
// Le modèle d'IA tourne dans la page (WebGPU) : le travailleur lui envoie le texte à lire et attend sa réponse.
const demandesIA = new Map();
let prochaineIA = 1;
function demanderIA(reqId, contexte) {
  const idIA = prochaineIA++;
  return new Promise((resolve, reject) => {
    demandesIA.set(idIA, { resolve, reject });
    envoyer({ type: "demande_ia", reqId, idIA, contexte });
  });
}

// Dernière version publiée (« Releases ») : les applications déjà prêtes à installer y mettent leurs fichiers
async function derniereVersion(depot) {
  try {
    const r = await fetch(`https://api.github.com/repos/${depot}/releases/latest`, { headers: { Accept: "application/vnd.github+json" } });
    if (!r.ok) return null;
    const d = await r.json();
    const fichiers = (d.assets || []).map((a) => ({ nom: a.name, url: a.browser_download_url, taille: a.size }));
    if (!fichiers.length) return null;
    return { nom: d.name || d.tag_name, tag: d.tag_name, date: d.published_at, page: d.html_url, fichiers };
  } catch (e) { return null; }
}

async function correspondNpm(nom, depot) {
  if (!nom) return false;
  try {
    const r = await fetch(`https://registry.npmjs.org/${nom.replace("/", "%2F")}`);
    if (!r.ok) return false;
    const d = await r.json();
    const urls = [JSON.stringify(d.repository || ""), d.homepage || "", JSON.stringify(d.bugs || "")].join(" ").toLowerCase();
    return urls.includes(`github.com/${depot.toLowerCase()}`);
  } catch (e) { return false; }
}

// Tous les fichiers utiles du dépôt (code + données), pour les lancer avec Node.js dans la page
async function fichiersPourNode(id, meta, journal) {
  let total = 0;
  const autres = meta.fichiers.filter(([p, t]) => !estLeger(p) && (total += t) <= LIMITES.octetsTotal).map(([p]) => p);
  if (autres.length) {
    journal(`Récupération des ${autres.length} autres fichiers du projet…`);
    await telechargerFichiers(id, meta, autres, journal);
  }
  const base = `/apps/${id}/code`, liste = [];
  (function parcourir(dossier) {
    for (const n of pyodide.FS.readdir(dossier)) {
      if (n === "." || n === "..") continue;
      const chemin = `${dossier}/${n}`;
      if (pyodide.FS.isDir(pyodide.FS.stat(chemin).mode)) parcourir(chemin);
      else { const o = pyodide.FS.readFile(chemin); liste.push({ chemin: chemin.slice(base.length + 1), octets: o.slice().buffer }); }
    }
  })(base);
  return liste;
}

async function correspondPyPI(nom, depot) {
  try {
    const r = await fetch(`https://pypi.org/pypi/${encodeURIComponent(nom)}/json`);
    if (!r.ok) return false;
    const d = await r.json();
    const urls = [d.info.home_page, d.info.download_url, ...Object.values(d.info.project_urls || {})]
      .filter(Boolean).join(" ").toLowerCase();
    return urls.includes(`github.com/${depot.toLowerCase()}`);
  } catch (e) { return false; }
}

// ---------------------------------------------------------------- étapes
async function telechargerEtInstaller(id, meta, { etape, journal }) {
  const legers = meta.fichiers.filter(([p]) => estLeger(p)).map(([p]) => p).slice(0, LIMITES.fichiersLegers);
  journal(`Récupération de ${legers.length} fichiers de code depuis GitHub…`);
  await telechargerFichiers(id, meta, legers, journal);
  etape("telechargement", "fait", `${legers.length} fichiers de code récupérés.`);

  return async function installer() {
    etape("installation", "en_cours");
    const infos = JSON.parse(moteur.infos_installation(id));
    let pypiOk = false;
    if (infos.nom) {
      journal(`Nom du projet : ${infos.nom}. Vérification sur PyPI…`);
      pypiOk = await correspondPyPI(infos.nom, meta.depot);
      journal(pypiOk ? "Version officielle trouvée sur PyPI." : "Pas de version officielle correspondante sur PyPI : utilisation du code du dépôt.");
    }
    const res = JSON.parse(await moteur.installer(id, pypiOk, journal));
    if (!res.paquet_installe) {
      // Le projet tourne depuis son code : il a aussi besoin de ses fichiers de données
      let total = 0;
      const autres = meta.fichiers.filter(([p, t]) => !estLeger(p) && (total += t) <= LIMITES.octetsTotal).map(([p]) => p);
      if (autres.length) {
        journal(`Récupération des ${autres.length} autres fichiers du projet…`);
        await telechargerFichiers(id, meta, autres, journal);
      }
    }
    paquetsInstalles.set(id, res);
    return res;
  };
}

async function installer(msg) {
  const { id } = msg;
  const etape = (cle, etat, detail) => envoyer({ type: "etape", reqId: msg.reqId, cle, etat, detail });
  const journal = (ligne) => envoyer({ type: "journal", reqId: msg.reqId, ligne: String(ligne) });
  let cle = "telechargement";
  try {
    etape("telechargement", "en_cours");
    await initialiser();
    journal("Lecture des informations du dépôt sur GitHub…");
    const meta = await lireDepot(msg.depot);
    if (meta.tronque) journal("Attention : ce dépôt est très gros, tous ses fichiers n'ont pas pu être listés.");
    nettoyer(id);
    const etapeInstallation = await telechargerEtInstaller(id, meta, { etape, journal });

    cle = "analyse";
    etape("analyse", "en_cours");
    journal("Lecture du code du projet…");
    const reglesJson = moteur.analyser(id, meta.depot, meta.langage || "");
    const regles = JSON.parse(reglesJson);
    let analyse = regles;
    const aDuCode = meta.fichiers.some(([p]) => /\.(py|js|mjs|cjs|ts)$/.test(p));
    if (msg.iaLocale && aDuCode) {
      try {
        journal("Analyse du projet par l'IA…");
        const contexte = moteur.contexte_ia(id, meta.depot, meta.description || "", reglesJson);
        const reponse = await demanderIA(msg.reqId, contexte);
        const v = JSON.parse(moteur.valider_ia(id, JSON.stringify(reponse), reglesJson));
        if (v.ok) { analyse = v.analyse; journal("L'IA a décrit l'interface."); }
        else if (v.ia) { analyse = { ok: false, raison: v.raison, description: regles.description }; journal(`L'IA estime que ce projet n'est pas utilisable : ${v.raison}`); }
        else journal(`Réponse de l'IA inutilisable (${v.raison}) : utilisation de l'analyse automatique.`);
      } catch (e) {
        journal(`L'IA n'a pas pu être utilisée (${e.message}) : utilisation de l'analyse automatique.`);
        envoyer({ type: "avertissement_ia", reqId: msg.reqId, message: e.message });
      }
    }
    if (!analyse.ok) {
      // Pas un outil en ligne de commande : c'est peut-être déjà une application, avec des fichiers à installer
      journal("Recherche d'une version prête à installer (Releases)…");
      const version = await derniereVersion(meta.depot);
      if (version) {
        etape("analyse", "fait", "Ce projet est déjà une application, avec des fichiers prêts à installer.");
        return { ok: false, application: true, version, langage: meta.langage, description: meta.description || analyse.description };
      }
      let raison = analyse.raison;
      if (meta.langage && meta.langage !== "Python" && !raison.includes(meta.langage)) raison += ` (langage principal de ce projet : ${meta.langage}).`;
      etape("analyse", "erreur", raison);
      return { ok: false, raison, description: meta.description || analyse.description };
    }
    analyse.source = analyse.source || "regles";
    journal(`Trouvé : ${analyse.outils.length} action(s) possible(s).`);
    etape("analyse", "fait", analyse.source === "ia" ? "Interface décrite par l'IA." : "Analyse automatique (sans IA).");

    if (analyse.langage === "javascript") {
      // Les outils JavaScript sont installés et lancés par la page (Node.js dans le navigateur)
      cle = "installation";
      etape("installation", "en_cours");
      const npm = await correspondNpm(analyse.lancement.paquet, meta.depot);
      journal(npm ? `Version officielle « ${analyse.lancement.paquet} » trouvée sur npm.` : "Pas de version officielle correspondante sur npm : utilisation du code du dépôt.");
      if (!npm && !analyse.lancement.source_presente) {
        const raison = "Le code de cette commande n'est pas dans le dépôt (il est fabriqué au moment de la publication) et aucune version publiée n'a été trouvée.";
        etape("installation", "erreur", raison);
        return { ok: false, raison, description: meta.description || analyse.description };
      }
      const fichiers = npm ? [] : await fichiersPourNode(id, meta, journal);
      const description = analyse.source === "ia" ? (analyse.description || meta.description) : (meta.description || analyse.description);
      return { ok: true, analyse, meta, description, npm, fichiers, problemes: [], paquet_installe: npm, javascript: true };
    }

    cle = "installation";
    const res = await etapeInstallation();
    const bloquants = res.problemes.filter((p) => LOURDS.test(p));
    if (bloquants.length) {
      const liste = [...new Set(bloquants.map((p) => p.split(/[<>=!~ ;\[]/)[0]))];
      const noms = liste.length > 1 ? `${liste.slice(0, -1).join(", ")} et ${liste[liste.length - 1]}` : liste[0];
      const raison = liste.length > 1
        ? `Cet outil a besoin de ${noms}, qui ne peuvent pas fonctionner dans un navigateur (bibliothèques trop lourdes ou liées au système). Utilisez la version Windows de Passerelle pour ce projet.`
        : `Cet outil a besoin de ${noms}, qui ne peut pas fonctionner dans un navigateur (bibliothèque trop lourde ou liée au système). Utilisez la version Windows de Passerelle pour ce projet.`;
      etape("installation", "erreur", raison);
      return { ok: false, raison, description: meta.description || analyse.description };
    }
    etape("installation", res.problemes.length ? "erreur" : "fait",
      res.problemes.length ? `Non installé dans le navigateur : ${res.problemes.join(", ")}` : (res.paquet_installe ? "Version officielle installée." : "Lancé depuis le code du dépôt."));
    etape("interface", "fait");
    prets.add(id);
    const description = analyse.source === "ia" ? (analyse.description || meta.description) : (meta.description || analyse.description);
    return { ok: true, analyse, meta, description, ...res };
  } catch (e) {
    const message = e instanceof ErreurClaire ? e.message : `Erreur inattendue : ${e && e.message || e}`;
    journal(`ERREUR : ${e && e.message || e}`);
    etape(cle, "erreur", message);
    return { ok: false, raison: message };
  }
}

async function preparer(msg) {
  const { app } = msg;
  if (prets.has(app.id)) return { ok: true, deja: true };
  const journal = (ligne) => envoyer({ type: "journal", reqId: msg.reqId, ligne: String(ligne) });
  try {
    await initialiser();
    nettoyer(app.id);
    const etapeInstallation = await telechargerEtInstaller(app.id, app.meta, { etape() {}, journal });
    const res = await etapeInstallation();
    prets.add(app.id);
    return { ok: true, ...res };
  } catch (e) {
    return { ok: false, raison: e instanceof ErreurClaire ? e.message : `Erreur inattendue : ${e && e.message || e}` };
  }
}

function nettoyer(id) {
  const racine = `/apps/${id}`;
  try { pyodide.runPython(`import shutil; shutil.rmtree(${JSON.stringify(racine)}, ignore_errors=True)`); } catch (e) {}
  prets.delete(id);
}

function lancer(msg) {
  const { app, indexOutil, valeurs, fichiers } = msg;
  const job = `/apps/${app.id}/taches/t${Date.now()}`;
  const entrees = {};
  for (const f of fichiers) {
    const dossier = `${job}/entrees/${f.param}`;
    pyodide.FS.mkdirTree(dossier);
    const nom = (f.nom || "fichier").replace(/[\\/]/g, "_").replace(/[^\w.\- ()]/g, "_");
    pyodide.FS.writeFile(`${dossier}/${nom}`, new Uint8Array(f.octets));
    (entrees[f.param] = entrees[f.param] || []).push(`${dossier}/${nom}`);
  }
  const paquet = (paquetsInstalles.get(app.id) || {}).paquet_installe ?? app.paquet_installe ?? false;
  const ecrire = (ligne) => envoyer({ type: "sortie", reqId: msg.reqId, ligne: String(ligne) });
  const code = moteur.executer(app.id, JSON.stringify(app.analyse.outils[indexOutil]), JSON.stringify(valeurs),
    JSON.stringify(entrees), job, JSON.stringify(app.analyse.lancement), paquet, ecrire);
  const liste = JSON.parse(moteur.resultats(job));
  const resultats = [], transfert = [];
  let total = 0;
  for (const r of liste) {
    total += r.taille;
    if (total > 200 * 1024 * 1024) break;
    const octets = pyodide.FS.readFile(`${job}/sorties/${r.nom}`);
    resultats.push({ ...r, octets: octets.buffer });
    transfert.push(octets.buffer);
  }
  return { reponse: { code, resultats }, transfert };
}

// ---------------------------------------------------------------- messages
onmessage = async (e) => {
  const msg = e.data;
  try {
    if (msg.type === "init") { await initialiser(); envoyer({ type: "fin", reqId: msg.reqId }); return; }
    if (msg.type === "installer") {
      const r = await installer(msg);
      envoyer({ type: "fin", reqId: msg.reqId, resultat: r }, (r.fichiers || []).map((f) => f.octets));
      return;
    }
    if (msg.type === "arguments") {
      await initialiser();
      envoyer({ type: "fin", reqId: msg.reqId, resultat: JSON.parse(moteur.construire_args(JSON.stringify(msg.outil), JSON.stringify(msg.valeurs), JSON.stringify(msg.entrees), msg.job)) });
      return;
    }
    if (msg.type === "fichiers_js") {
      await initialiser();
      const journal = (ligne) => envoyer({ type: "journal", reqId: msg.reqId, ligne: String(ligne) });
      nettoyer(msg.id);
      const legers = msg.meta.fichiers.filter(([p]) => estLeger(p)).map(([p]) => p).slice(0, LIMITES.fichiersLegers);
      await telechargerFichiers(msg.id, msg.meta, legers, journal);
      const fichiers = await fichiersPourNode(msg.id, msg.meta, journal);
      envoyer({ type: "fin", reqId: msg.reqId, resultat: fichiers }, fichiers.map((f) => f.octets));
      return;
    }
    if (msg.type === "reponse_ia") {
      const d = demandesIA.get(msg.idIA);
      if (d) { demandesIA.delete(msg.idIA); msg.erreur ? d.reject(new ErreurClaire(msg.erreur)) : d.resolve(msg.reponse); }
      return;
    }
    if (msg.type === "preparer") { envoyer({ type: "fin", reqId: msg.reqId, resultat: await preparer(msg) }); return; }
    if (msg.type === "lancer") {
      await initialiser();
      const { reponse, transfert } = lancer(msg);
      envoyer({ type: "fin", reqId: msg.reqId, resultat: reponse }, transfert);
      return;
    }
    throw new Error(`Demande inconnue : ${msg.type}`);
  } catch (err) {
    envoyer({ type: "erreur", reqId: msg.reqId, message: err instanceof ErreurClaire ? err.message : String(err && err.message || err) });
  }
};
