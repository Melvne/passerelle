/*
 * Node.js dans le navigateur, pour les outils JavaScript (grâce à WebContainers de StackBlitz).
 * Un seul « ordinateur virtuel » Node par page : on y installe chaque outil dans son dossier,
 * puis on le lance avec les arguments construits à partir du formulaire.
 */
"use strict";

const NODE_API = "https://cdn.jsdelivr.net/npm/@webcontainer/api@1.6.4/+esm";
const NODE_RACINE = "/home/passerelle";
let nodeVM = null, demarrageNode = null;
const appsNodePretes = new Map();   // id → chemin absolu du programme à lancer
let processusNode = null;

const sansCouleurs = (t) => t.replace(/\x1b\[[0-9;?]*[ -\/]*[@-~]/g, "").replace(/\x1b\][^\x07]*\x07/g, "").replace(/\r(?!\n)/g, "\n");

function demarrerNode() {
  if (nodeVM) return Promise.resolve(nodeVM);
  if (!demarrageNode) {
    demarrageNode = (async () => {
      if (!window.crossOriginIsolated) {
        throw new Error("Ce navigateur n'a pas activé le mode nécessaire aux outils JavaScript. Rechargez la page ; si le problème continue, utilisez Chrome ou Edge à jour.");
      }
      const { WebContainer } = await import(NODE_API);
      nodeVM = await WebContainer.boot({ coep: "credentialless", workdirName: "passerelle" });
      return nodeVM;
    })().catch((e) => { demarrageNode = null; throw e; });
  }
  return demarrageNode;
}

async function executerNode(commande, args, options, surLigne) {
  const vm = await demarrerNode();
  const p = await vm.spawn(commande, args, { ...options, env: { FORCE_COLOR: "0", NO_COLOR: "1", CI: "1", ...(options && options.env) } });
  processusNode = p;
  let reste = "";
  p.output.pipeTo(new WritableStream({
    write(morceau) {
      const lignes = sansCouleurs(reste + morceau).split("\n");
      reste = lignes.pop();
      lignes.forEach((l) => surLigne && surLigne(l));
    },
  })).catch(() => {});
  const code = await p.exit;
  if (reste && surLigne) surLigne(reste);
  if (processusNode === p) processusNode = null;
  return code;
}

function arreterNode() { if (processusNode) { try { processusNode.kill(); } catch (e) {} processusNode = null; } }

async function ecrireFichiersNode(dossier, fichiers) {
  const vm = await demarrerNode();
  for (const f of fichiers) {
    const chemin = `${dossier}/${f.chemin}`;
    await vm.fs.mkdir(chemin.split("/").slice(0, -1).join("/"), { recursive: true });
    await vm.fs.writeFile(chemin, new Uint8Array(f.octets));
  }
}

/*
 * Installe un outil JavaScript. Depuis npm quand la version officielle correspond au dépôt,
 * sinon depuis le code du dépôt (fichiers fournis par le travailleur).
 * Retourne le chemin du programme à lancer.
 */
async function installerNode(app, fichiers, journal) {
  const vm = await demarrerNode();
  const L = app.analyse.lancement;
  const dossier = `${NODE_RACINE}/apps/${app.id}`;
  try { await vm.fs.rm(dossier, { recursive: true, force: true }); } catch (e) {}
  await vm.fs.mkdir(dossier, { recursive: true });
  const npmOptions = ["--no-audit", "--no-fund", "--omit=dev", "--loglevel=error"];
  let programme;
  if (app.npm) {
    journal(`Installation de « ${L.paquet} » depuis npm…`);
    await vm.fs.writeFile(`${dossier}/package.json`, JSON.stringify({ name: "passerelle-app", private: true }));
    const code = await executerNode("npm", ["install", L.paquet, ...npmOptions], { cwd: dossier }, journal);
    if (code !== 0) throw new Error(`l'installation depuis npm a échoué (code ${code})`);
    // Le chemin du programme dans la version publiée (il peut différer de celui du dépôt)
    const pkg = JSON.parse(await vm.fs.readFile(`${dossier}/node_modules/${L.paquet}/package.json`, "utf-8"));
    const bin = typeof pkg.bin === "string" ? pkg.bin : (pkg.bin || {})[L.bin] || Object.values(pkg.bin || {})[0];
    if (!bin) throw new Error("la version publiée ne déclare pas de commande");
    programme = `${dossier}/node_modules/${L.paquet}/${bin.replace(/^\.\//, "")}`;
  } else {
    journal(`Copie des ${fichiers.length} fichiers du projet…`);
    await ecrireFichiersNode(`${dossier}/code`, fichiers);
    journal("Installation des dépendances avec npm…");
    const code = await executerNode("npm", ["install", ...npmOptions], { cwd: `${dossier}/code` }, journal);
    if (code !== 0) throw new Error(`l'installation des dépendances a échoué (code ${code})`);
    programme = `${dossier}/code/${L.fichier}`;
  }
  appsNodePretes.set(app.id, programme);
  journal("Installé.");
  return programme;
}

async function listerResultatsNode(dossier, base = dossier) {
  const vm = await demarrerNode();
  const resultats = [];
  let entrees = [];
  try { entrees = await vm.fs.readdir(dossier, { withFileTypes: true }); } catch (e) { return resultats; }
  for (const e of entrees) {
    const chemin = `${dossier}/${e.name}`;
    if (e.isDirectory()) resultats.push(...await listerResultatsNode(chemin, base));
    else {
      const octets = await vm.fs.readFile(chemin);
      const nom = chemin.slice(base.length + 1);
      const ext = (nom.split(".").pop() || "").toLowerCase();
      resultats.push({
        nom, taille: octets.length, octets: octets.buffer.slice(octets.byteOffset, octets.byteOffset + octets.length),
        type: ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"].includes(ext) ? "image" : (["txt", "md", "csv", "json", "html", "xml", "log"].includes(ext) ? "texte" : "autre"),
      });
    }
  }
  return resultats;
}

/* Lance un outil JavaScript déjà installé. args vient du travailleur (même logique que pour Python). */
async function lancerNode(app, fichiersEntree, construireArgs, surLigne) {
  const vm = await demarrerNode();
  const programme = appsNodePretes.get(app.id);
  if (!programme) throw new Error("l'outil n'est pas encore prêt");
  const job = `${NODE_RACINE}/jobs/t${Date.now()}`;
  await vm.fs.mkdir(`${job}/sorties`, { recursive: true });
  const entrees = {};
  for (const f of fichiersEntree) {
    const nom = (f.nom || "fichier").replace(/[\\/]/g, "_").replace(/[^\w.\- ()]/g, "_");
    const dossier = `${job}/entrees/${f.param}`;
    await vm.fs.mkdir(dossier, { recursive: true });
    await vm.fs.writeFile(`${dossier}/${nom}`, new Uint8Array(f.octets));
    (entrees[f.param] = entrees[f.param] || []).push(`${dossier}/${nom}`);
  }
  const args = await construireArgs(entrees, job);
  // Les dossiers de sortie demandés par le formulaire doivent exister avant le lancement
  for (const a of args) if (typeof a === "string" && a.startsWith(`${job}/sorties/`) && !/\.\w{1,5}$/.test(a)) await vm.fs.mkdir(a, { recursive: true });
  surLigne(`[passerelle] arguments : ${JSON.stringify(args)}`);
  const code = await executerNode("node", [programme, ...args], { cwd: `${job}/sorties` }, surLigne);
  return { code, resultats: await listerResultatsNode(`${job}/sorties`) };
}
