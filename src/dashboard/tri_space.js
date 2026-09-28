import * as THREE from "/assets/three.module.min.js";

/* The execution topology, drawn as a brain.
 *
 * Two things were wrong with the version this replaces.
 *
 * A phone never saw it. `setView(window.innerWidth > 767 ...)` sent anything
 * under 768px to the flat 2D ring, and the phone is the surface this system is
 * actually operated from. The 3D view worked the whole time; it was gated off
 * from the only place that mattered. It is the default now, on every width,
 * and the 2D view stays one tap away for a GPU that refuses.
 *
 * The layout was an even scatter on a sphere. What is wanted is something that
 * reads as a brain - two lobes, a fissure between them, a folded surface -
 * with signals firing across it. That is a shape and a wiring, not a paint job,
 * so both are built here:
 *
 *   brainShape   deforms a unit sphere into the silhouette: an ellipsoid
 *                longer than it is tall, split at the midline, with the
 *                surface folded so it reads as cortex.
 *   membrane     the same deformation applied to a wireframe shell, so the
 *                form is legible even when few nodes exist.
 *   synapses     nearest-neighbour wiring. Real task edges are sparse; most
 *                nodes would otherwise float unconnected, and an unconnected
 *                scatter is not a brain.
 *   signals      points travelling those synapses. This is the firing.
 *
 * Everything is budgeted for a phone GPU: one LineSegments for every synapse
 * rather than one object each, a bounded signal count, and a capped pixel
 * ratio. Motion stops on `prefers-reduced-motion` and on the pause control,
 * and the pause has to actually stop the firing or it is decoration.
 */

const root = document.getElementById("spatialGraph");
const panel = root && root.closest(".graph-panel");
const button3d = document.getElementById("graph3d");
const button2d = document.getElementById("graph2d");
const motionButton = document.getElementById("motionToggle");
const tooltip = document.getElementById("spatialTooltip");

// Budgets. Phones render this beside everything else they are doing.
const MAX_SIGNALS = 72;
const NEIGHBOURS_PER_NODE = 2;
const MEMBRANE_DETAIL = 3;
const MOBILE_FILE_POINT_BUDGET = 26000;
const DESKTOP_FILE_POINT_BUDGET = 48000;

function isCompactViewport() {
  return window.matchMedia(
    "(max-width: 760px), (pointer: coarse) and (orientation: portrait)",
  ).matches;
}

// How far each hemisphere is pushed off the midline. The gap is the single
// feature that makes the silhouette read as a brain rather than as a ball.
const FISSURE = 0.035;

// Depth of the surface folding. Enough to break the sphere, little enough
// that node positions stay readable.
const GYRI = 0.025;

if (root && panel && button3d && button2d && motionButton && tooltip) {
  try {
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.setClearColor(0x05070a, 0);
    root.prepend(renderer.domElement);

    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x05070a, 0.028);
    const camera = new THREE.PerspectiveCamera(44, 1, 0.1, 100);
    const topology = new THREE.Group();
    scene.add(topology);
    scene.add(new THREE.AmbientLight(0x9ddcff, 1.1));
    const keyLight = new THREE.PointLight(0x00f0ff, 45, 45);
    keyLight.position.set(4, 6, 8);
    scene.add(keyLight);
    const warmLight = new THREE.PointLight(0xffb703, 18, 32);
    warmLight.position.set(-8, -3, 4);
    scene.add(warmLight);

    /* The deformation. `unit` is a point on the unit sphere; the result is
     * that point moved onto a brain-shaped surface of radius 1-ish, to be
     * scaled by the caller's shell. */
    function brainShape(unit) {
      const p = unit.clone();
      // A nearly spherical neural volume matches the observatory reference;
      // a shallow fissure and restrained cortical folding keep it organic.
      p.x *= 1.03;
      p.y *= 0.98;
      p.z *= 1.03;
      // Split the hemispheres: every point moves away from the midline, so a
      // valley opens along it instead of a seam through a solid ball.
      const side = p.z >= 0 ? 1 : -1;
      p.z = side * (Math.abs(p.z) * 0.96 + FISSURE);
      // Fold the surface. Two frequencies, so the folds do not repeat
      // regularly enough to read as a pattern.
      const fold = 1
        + GYRI * Math.sin(p.x * 5.2) * Math.cos(p.z * 4.6)
        + GYRI * 0.6 * Math.sin(p.y * 6.1 + p.x * 2.0);
      p.multiplyScalar(fold);
      return p;
    }

    const core = new THREE.Mesh(
      new THREE.IcosahedronGeometry(0.16, 1),
      new THREE.MeshBasicMaterial({ color: 0x73e6c5, wireframe: true, transparent: true, opacity: 0.32 }),
    );
    topology.add(core);

    /* The outer shell, so the brain is a form and not only a cloud. Built by
     * pushing a subdivided icosahedron's vertices through the same
     * deformation the nodes use, which keeps shell and contents agreeing. */
    function buildMembrane(radius) {
      const geometry = new THREE.IcosahedronGeometry(1, MEMBRANE_DETAIL);
      const position = geometry.attributes.position;
      const vertex = new THREE.Vector3();
      for (let index = 0; index < position.count; index += 1) {
        vertex.fromBufferAttribute(position, index).normalize();
        const shaped = brainShape(vertex).multiplyScalar(radius);
        position.setXYZ(index, shaped.x, shaped.y, shaped.z);
      }
      position.needsUpdate = true;
      geometry.computeVertexNormals();
      const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
        color: 0x1d7f9c, wireframe: true, transparent: true, opacity: 0.035,
      }));
      // Survives a rebuild. The shell is the form itself, not contents, and
      // recomputing 1,280 deformed vertices on every snapshot would be waste.
      mesh.userData.membrane = true;
      return mesh;
    }
    topology.add(buildMembrane(6.75));

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let motionPaused = reducedMotion.matches;
    let snapshot = null;
    let selectedId = null;
    let meshes = [];
    let meshById = new Map();
    let dynamicMaterials = [];
    let synapses = null;
    let synapseSegments = [];
    let signals = null;
    let signalState = [];
    let fileCloud = null;
    let fileItems = [];
    let visualFileNodeCount = 0;
    let filePositions = new Map();
    let lastRenderSignature = null;
    // A portrait screen needs the two lobes head-on. The wider console can
    // afford the more oblique observatory angle.
    let yaw = isCompactViewport() ? 0.08 : 0.42;
    let pitch = isCompactViewport() ? 0.08 : 0.18;
    // The organ is the primary information object, not background decoration.
    // This framing keeps the full cortex visible while letting its firing read
    // at a glance in the desktop dashboard's wide topology panel.
    // The architecture layer extends beyond the cortical core. Keep its outer
    // source and release nodes in frame before offering manual zoom.
    let distance = isCompactViewport() ? 16.7 : 15.9;
    let drag = null;

    const colors = {
      task: 0x00f0ff,
      rule: 0xffb703,
      brain: 0xa78bfa,
      capability: 0x38bdf8,
      radar: 0xf472b6,
      done: 0x00ff9d,
      failed: 0xff4d6d,
      ingress: 0x8de0bf,
      source: 0x63d9b6,
      planner: 0x9fe8cf,
      model: 0x7bbde5,
      specialist: 0xe9b86d,
      verifier: 0xedb66f,
      approval: 0xef9a86,
    };

    function applyCamera() {
      const cp = Math.cos(pitch);
      camera.position.set(
        Math.sin(yaw) * cp * distance,
        Math.sin(pitch) * distance,
        Math.cos(yaw) * cp * distance,
      );
      camera.lookAt(0, 0, 0);
    }

    function resize() {
      const rect = root.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      renderer.setSize(rect.width, rect.height, false);
      camera.aspect = rect.width / rect.height;
      camera.updateProjectionMatrix();
      applyCamera();
    }

    function hashUnit(value) {
      let hash = 2166136261;
      for (const char of String(value)) {
        hash ^= char.charCodeAt(0);
        hash = Math.imul(hash, 16777619);
      }
      return (hash >>> 0) / 4294967295;
    }

    /* Shells are banded tightly so the whole thing reads as one organ with
     * depth, rather than as separate nested spheres. */
    const SHELLS = { brain: 3.8, task: 5.2, capability: 5.8, rule: 6.2, radar: 6.6 };

    function positionFor(node, index, total) {
      if (node.anchor) return node.anchor.clone();
      const shell = SHELLS[node.kind] || 5.4;
      const seed = hashUnit(node.id);
      const y = 1 - 2 * ((index + seed) / Math.max(total, 1) % 1);
      const radial = Math.sqrt(Math.max(0.08, 1 - y * y));
      const angle = Math.PI * 2 * ((index * 0.61803398875 + seed) % 1);
      const unit = new THREE.Vector3(
        Math.cos(angle) * radial,
        y,
        Math.sin(angle) * radial,
      );
      return brainShape(unit).multiplyScalar(shell);
    }

    function geometryFor(kind) {
      if (kind === "rule") return new THREE.BoxGeometry(0.46, 0.46, 0.46);
      if (kind === "brain") return new THREE.IcosahedronGeometry(0.38, 1);
      if (kind === "capability") return new THREE.TetrahedronGeometry(0.36, 0);
      if (kind === "radar") return new THREE.OctahedronGeometry(0.33, 0);
      if (kind === "source") return new THREE.OctahedronGeometry(0.42, 0);
      if (kind === "ingress") return new THREE.SphereGeometry(0.48, 18, 12);
      if (kind === "planner") return new THREE.DodecahedronGeometry(0.56, 0);
      if (kind === "model") return new THREE.IcosahedronGeometry(0.49, 1);
      if (kind === "specialist") return new THREE.BoxGeometry(0.48, 0.48, 0.48);
      if (kind === "verifier") return new THREE.CylinderGeometry(0.46, 0.46, 0.2, 6);
      if (kind === "approval") return new THREE.TorusGeometry(0.42, 0.12, 8, 20);
      return new THREE.SphereGeometry(0.4, 16, 10);
    }

    function scaleFor(kind) {
      // The fixed-size beacon is the information signal at console distance.
      // Physical objects stay deliberately compact so the connected cortex,
      // not a pile of oversized tokens, remains the visual protagonist.
      if (kind === "planner" || kind === "approval") return 0.18;
      if (kind === "model" || kind === "verifier") return 0.15;
      if (kind === "specialist") return 0.14;
      if (kind === "source" || kind === "ingress") return 0.13;
      if (kind === "brain") return 0.13;
      return 0.12;
    }

    function nodeColor(node) {
      if (node.kind === "task") {
        if (node.detail.status === "done") return colors.done;
        if (node.detail.status === "failed" || node.detail.status === "cancelled") return colors.failed;
      }
      return colors[node.kind] || colors.task;
    }

    function disposeObject(child) {
      child.geometry?.dispose();
      if (Array.isArray(child.material)) child.material.forEach((material) => material.dispose());
      else child.material?.dispose();
    }

    function clearTopology() {
      for (const child of [...topology.children]) {
        // The core and the membrane are the body; everything else is contents
        // and is rebuilt from the snapshot.
        if (child === core || child.userData?.membrane) continue;
        topology.remove(child);
        disposeObject(child);
      }
      meshes = [];
      meshById = new Map();
      dynamicMaterials = [];
      synapses = null;
      synapseSegments = [];
      signals = null;
      signalState = [];
      fileCloud = null;
      fileItems = [];
      visualFileNodeCount = 0;
      filePositions = new Map();
    }

    function architectureNodes() {
      const activeLane = root.dataset.activeLane || "claude";
      // The console camera intentionally holds the whole decision path in one
      // view. Keep source, routing, and approval nodes inside that shared
      // field rather than sending the outermost systems beyond its edges.
      const node = (id, kind, label, anchor, detail) => {
        const [x, y, z] = anchor;
        return {
          id,
          kind,
          label,
          anchor: new THREE.Vector3(x * 0.64, y * 0.72, z * 0.72),
          detail: { system: true, ...detail },
        };
      };
      return [
        node("system:ingress", "ingress", "Telegram / voice", [-8.5, -4.2, 1.8], { group: "Ingress", description: "A Telegram or voice request becomes a bounded brief before it reaches a planner.", context: "Request text or voice transcript", tools: "Telegram, voice interface", evidence: "Bounded brief with operator scope" }),
        node("system:obsidian", "source", "Obsidian", [-9.6, 3.8, 1.8], { group: "Authorized world", description: "An opt-in local note source. Raw content remains private and memories retain source provenance.", context: "User-authorized notes", tools: "Local Obsidian adapter", evidence: "Source reference and retrieval record" }),
        node("system:drive", "source", "Drive", [-10.1, 1.8, 2.8], { group: "Authorized world", description: "An opt-in file source. Tri-AI indexes only selected folders and retains the source record.", context: "Approved files and folders", tools: "Drive connector", evidence: "File provenance and retrieval record" }),
        node("system:github", "source", "GitHub", [-9.8, -0.1, 2.3], { group: "Authorized world", description: "A repository source used for bounded code context, commit history, and produced artifacts.", context: "Approved repositories", tools: "GitHub connector", evidence: "Commit, diff, and repository reference" }),
        node("system:deploys", "source", "Vercel / Render", [-9.2, -2.0, 1.2], { group: "Authorized world", description: "Deployment context is read only until a human approves an external release action.", context: "Approved deployment metadata", tools: "Vercel and Render adapters", evidence: "Preview, deployment, and rollback record" }),
        node("system:sessions", "source", "Claude / Codex sessions", [-8.8, 0.6, -2.7], { group: "Authorized world", description: "User-authorized exports or local session records provide handoffs, plans, and artifacts without pretending to read every conversation.", context: "Selected session exports", tools: "Local session importer", evidence: "Conversation or handoff provenance" }),
        node("system:planner", "planner", "Tri-AI planner", [-5.9, 0.1, 1.1], { group: "Control plane", description: "The planner maps a request to bounded specialist tasks and chooses an appropriate execution lane.", context: "Approved brief, source boundaries, task policy", tools: "Route registry and task board", evidence: "Build contract and specialist task records" }),
        node("system:model:claude", "model", "Claude Code", [-2.7, 4.3, 1.5], { group: "Reasoning lane", description: "A subscription-backed Claude Code lane for planning, research, and repository work when that lane is selected.", context: "Approved brief and source pack", tools: "Research, files, documentation", evidence: "Plan, citations, and acceptance checks", active: activeLane === "claude" }),
        node("system:model:codex", "model", "Codex", [0.2, 4.7, 1.6], { group: "Reasoning lane", description: "A Codex lane for implementation, test execution, browser inspection, and reviewable delivery packets.", context: "Build contract and repository scope", tools: "Code, browser checks, test harness", evidence: "Diff, test evidence, and rollback packet", active: activeLane === "codex" }),
        node("system:model:omniroute", "model", "OmniRoute", [3.0, 3.8, 1.8], { group: "Free routing", description: "The local OmniRoute gateway selects an admitted free model route. It is a router, not a claim that every provider is unlimited.", context: "Bounded task plus route policy", tools: "Local route registry", evidence: "Requested and resolved model record", active: activeLane === "local" }),
        node("system:model:freellmapi", "model", "FreeLLMAPI", [4.8, 1.8, 2.1], { group: "Free routing", description: "A separate free-token model route used only when its availability and policy allow it. Usage remains visible and bounded.", context: "Bounded task plus token policy", tools: "FreeLLMAPI adapter", evidence: "Provider, model, and usage record", active: activeLane === "local" }),
        node("system:model:ollama", "model", "Ollama local", [4.9, -1.2, 1.6], { group: "Local execution", description: "Local Ollama models provide offline, zero-marginal-cost execution for eligible subtasks.", context: "Bounded task plus local workspace", tools: "Ollama runtime", evidence: "Local model, transcript, and verifier input", active: activeLane === "local" }),
        node("system:research", "specialist", "Research worker", [6.6, 4.1, 0.5], { group: "Specialist work", description: "Researches the request, distinguishes sources from claims, and returns citations for review.", context: "Approved research brief", tools: "Search and source analysis", evidence: "Cited research record" }),
        node("system:design", "specialist", "Design worker", [7.8, 1.9, 0.3], { group: "Specialist work", description: "Creates interface direction, visual references, and testable interaction requirements.", context: "Product brief and design constraints", tools: "Design and browser tools", evidence: "UI specification and visual checks" }),
        node("system:build", "specialist", "Build worker", [7.8, -0.7, 0.5], { group: "Specialist work", description: "Implements bounded code changes in an isolated workspace and records the resulting diff.", context: "Build contract and repository scope", tools: "Code, tests, and local runtime", evidence: "Diff, tests, and artifact record" }),
        node("system:verify", "verifier", "Verifier", [8.2, -3.5, 0.3], { group: "Verification", description: "A task is accepted only when the recorded verifier command succeeds, never just because an agent reports success.", context: "Candidate, acceptance checks, and evidence", tools: "Test and review harness", evidence: "Pass or failure record with output" }),
        node("system:brain", "brain", "Retained brain", [1.0, -4.3, -1.2], { group: "Memory", description: "Retains scoped handoffs, rules, citations, and outcome records with provenance rather than claiming universal memory.", context: "Accepted evidence only", tools: "Memory and graph store", evidence: "Provenance-linked memory nodes" }),
        node("system:approval", "approval", "Human approval", [10.8, -1.8, 0.1], { group: "Delivery", description: "Deployments, publishing, DNS, credentials, and paid actions remain behind explicit operator approval.", context: "Release proposal, diff, tests, rollback", tools: "Approval gate", evidence: "Decision and release ledger" }),
      ];
    }

    function modelFrom(data) {
      const brain = data.brain || { items: [] };
      const capabilities = data.capabilities || { items: [] };
      const radar = data.radar || { candidates: [] };
      return [
        ...architectureNodes(),
        ...data.tasks.map((detail) => ({ id: `task:${detail.id}`, kind: "task", label: detail.prompt || detail.title, detail })),
        ...data.rules.map((detail) => ({ id: `rule:${detail.proposal_id}`, kind: "rule", label: detail.rule_id, detail })),
        ...(brain.items || []).map((detail) => ({ id: `brain:${detail.id}`, kind: "brain", label: detail.title, detail })),
        ...(capabilities.items || []).map((detail) => ({ id: `capability:${detail.id}`, kind: "capability", label: detail.name, detail })),
        ...(radar.candidates || []).map((detail) => ({ id: `radar:${detail.source}:${detail.name}`, kind: "radar", label: detail.name, detail })),
      ];
    }

    function addEdge(sourceId, targetId, color, opacity) {
      const source = meshById.get(sourceId);
      const target = meshById.get(targetId);
      if (!source || !target) return;
      const middle = source.position.clone().add(target.position).multiplyScalar(0.5);
      middle.normalize().multiplyScalar(Math.max(source.position.length(), target.position.length()) + 0.75);
      const curve = new THREE.QuadraticBezierCurve3(source.position, middle, target.position);
      const geometry = new THREE.BufferGeometry().setFromPoints(curve.getPoints(20));
      const material = new THREE.LineBasicMaterial({ color, transparent: true, opacity });
      topology.add(new THREE.Line(geometry, material));
    }

    /* Nearest-neighbour wiring. The board's real edges are sparse - most tasks
     * have no parent - so without this the majority of nodes float unconnected
     * and the thing reads as dust rather than tissue. These are drawn dimmer
     * than real edges on purpose: they are structure, not evidence. */
    function buildSynapses() {
      synapseSegments = [];
      if (meshes.length < 2) return;
      const points = [];
      const seen = new Set();
      for (let i = 0; i < meshes.length; i += 1) {
        const from = meshes[i].position;
        const ranked = [];
        for (let j = 0; j < meshes.length; j += 1) {
          if (i === j) continue;
          ranked.push([from.distanceToSquared(meshes[j].position), j]);
        }
        ranked.sort((a, b) => a[0] - b[0]);
        for (let k = 0; k < Math.min(NEIGHBOURS_PER_NODE, ranked.length); k += 1) {
          const j = ranked[k][1];
          const key = i < j ? `${i}:${j}` : `${j}:${i}`;
          if (seen.has(key)) continue;
          seen.add(key);
          const to = meshes[j].position;
          points.push(from.x, from.y, from.z, to.x, to.y, to.z);
          synapseSegments.push([from.clone(), to.clone()]);
        }
      }
      if (!points.length) return;
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.Float32BufferAttribute(points, 3));
      synapses = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({
        color: 0x2de2e6, transparent: true, opacity: 0.22,
      }));
      topology.add(synapses);
    }

    function filePosition(item, index, total, sourceOrder) {
      if (String(item.id).startsWith("ambient:")) {
        // The private scene carries counts rather than file metadata. Spread
        // the representative points over both lobes with a Fibonacci field:
        // source colours still reveal origin, but one very large source can
        // no longer fill a single screen-facing wedge into a bright wall.
        const y = 1 - 2 * ((index + 0.5) / Math.max(total, 1));
        const radial = Math.sqrt(Math.max(0.08, 1 - y * y));
        const angle = index * 2.399963229728653 + hashUnit(`${item.id}:phase`) * 0.36;
        const unit = new THREE.Vector3(Math.cos(angle) * radial, y, Math.sin(angle) * radial);
        const depth = 1.2 + Math.pow(hashUnit(`${item.id}:depth`), 0.62) * 4.75;
        return brainShape(unit).multiplyScalar(depth);
      }
      const sourceIndex = Math.max(0, sourceOrder.indexOf(item.source));
      const sourceCount = Math.max(1, sourceOrder.length);
      const sourceAngle = (sourceIndex / sourceCount) * Math.PI * 2;
      const seed = hashUnit(item.id);
      const secondary = hashUnit(`${item.source}:${item.label}`);
      const angle = sourceAngle + (seed - 0.5) * 1.72 + index * 0.017;
      const y = Math.max(-0.96, Math.min(0.96, secondary * 1.92 - 0.96));
      const radial = Math.sqrt(Math.max(0.08, 1 - y * y));
      const unit = new THREE.Vector3(Math.cos(angle) * radial, y, Math.sin(angle) * radial);
      const density = Math.pow(hashUnit(`${item.id}:depth`), 0.42);
      const depth = item.kind === "folder" ? 5.7 + seed * 0.65 : 0.45 + density * 5.95;
      return brainShape(unit).multiplyScalar(depth);
    }

    function proceduralAmbientItems(fileGraph) {
      if (fileGraph?.ambient?.mode !== "procedural-source-density") return null;
      return Array.isArray(fileGraph.ambient.sources) ? fileGraph.ambient.sources : [];
    }

    function buildFileClusters(fileGraph, sourceOrder) {
      const clusters = Array.isArray(fileGraph?.clusters) ? fileGraph.clusters : [];
      clusters.forEach((cluster, index) => {
        const angle = (index / Math.max(1, clusters.length)) * Math.PI * 2;
        const unit = new THREE.Vector3(Math.cos(angle), 0.18 + (index % 2) * 0.14, Math.sin(angle)).normalize();
        const node = fileNode({
          id: cluster.id, label: cluster.label, kind: "cluster", source: cluster.source,
          node_count: cluster.node_count, folder_count: cluster.folder_count,
        });
        if (!node) return;
        const pending = cluster.state === "pending";
        const material = new THREE.MeshStandardMaterial({
          color: pending ? 0xffb703 : nodeColor({ kind: "brain" }),
          emissive: pending ? 0xffb703 : 0x63d9b6,
          emissiveIntensity: pending ? 0.38 : 0.9,
          metalness: 0.12, roughness: 0.32, transparent: true, opacity: pending ? 0.56 : 0.98,
        });
        const mesh = new THREE.Mesh(new THREE.IcosahedronGeometry(0.23, 2), material);
        mesh.position.copy(brainShape(unit).multiplyScalar(6.15));
        mesh.userData = node;
        mesh.renderOrder = 4;
        topology.add(mesh);
        meshes.push(mesh);
        dynamicMaterials.push({ mesh, material, baseScale: 1 });
        synapseSegments.push([mesh.position.clone(), new THREE.Vector3()]);
      });
    }

    /* The public demonstration uses explicit synthetic records. A live private
     * scene uses procedural-source-density: exact counts become GPU points,
     * but file names and paths never cross the initial browser boundary. */
    function buildFileUniverse(fileGraph) {
      const ambientSources = proceduralAmbientItems(fileGraph);
      fileItems = ambientSources ? [] : (Array.isArray(fileGraph?.items) ? fileGraph.items : []);
      const visualItems = ambientSources ? null : fileItems;
      const total = ambientSources
        ? ambientSources.reduce((sum, source) => sum + Math.max(0, Number(source.node_count) || 0), 0)
        : visualItems.length;
      const representedNodeCount = total;
      const pointBudget = isCompactViewport() ? MOBILE_FILE_POINT_BUDGET : DESKTOP_FILE_POINT_BUDGET;
      const renderedPointCount = ambientSources ? Math.min(total, pointBudget) : total;
      visualFileNodeCount = representedNodeCount;
      if (!total) return;
      const sourceOrder = (fileGraph.sources || []).map((source) => source.id);
      const positions = new Float32Array(renderedPointCount * 3);
      const colors = new Float32Array(renderedPointCount * 3);
      const sourceColors = [0x54d7ae, 0x7ce8c5, 0x48b996, 0x9af3d5, 0x3f9f83, 0x6cd9b8, 0xb1f7df];
      const folder = new THREE.Color(0xe7f8ef);
      const sourceRanges = [];
      let rangeEnd = 0;
      (ambientSources || []).forEach((source) => {
        rangeEnd += Math.max(0, Number(source.node_count) || 0);
        sourceRanges.push({ ...source, end: rangeEnd });
      });
      let sourceRangeIndex = 0;
      for (let renderIndex = 0; renderIndex < renderedPointCount; renderIndex += 1) {
        const index = renderedPointCount === total
          ? renderIndex
          : Math.min(total - 1, Math.floor(renderIndex * total / renderedPointCount));
        while (ambientSources && index >= sourceRanges[sourceRangeIndex].end) sourceRangeIndex += 1;
        const source = ambientSources ? sourceRanges[sourceRangeIndex] : null;
        const sourceStart = source ? source.end - source.node_count : 0;
        const item = visualItems ? visualItems[index] : {
          id: `ambient:${source.source}:${index - sourceStart}`,
          source: source.source,
          kind: index - sourceStart < source.folder_count ? "folder" : "file",
          label: source.source,
        };
        const position = filePosition(item, index, total, sourceOrder);
        if (visualItems) filePositions.set(item.id, position);
        positions.set([position.x, position.y, position.z], renderIndex * 3);
        const sourceIndex = Math.max(0, sourceOrder.indexOf(item.source));
        const color = item.kind === "folder" && !ambientSources ? folder : new THREE.Color(sourceColors[sourceIndex % sourceColors.length]);
        colors.set([color.r, color.g, color.b], renderIndex * 3);
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
      fileCloud = new THREE.Points(geometry, new THREE.PointsMaterial({
        size: isCompactViewport() ? 0.92 : total > 7000 ? 1.08 : total > 2500 ? 1.65 : 2.5,
        sizeAttenuation: false,
        vertexColors: true,
        transparent: true,
        opacity: isCompactViewport() ? 0.38 : 0.5,
        // The detailed field is data, not a light source. Normal blending
        // preserves colored regions and prevents large sources becoming a
        // white rectangle; only moving signals use additive glow below.
        blending: THREE.NormalBlending,
        depthWrite: false,
      }));
      fileCloud.userData.fileUniverse = true;
      fileCloud.userData.representedNodeCount = representedNodeCount;
      fileCloud.renderOrder = 3;
      topology.add(fileCloud);

      const branches = [];
      const edgeBudgetRatio = Math.min(1, 1900 / Math.max(fileItems.length, 1));
      fileItems.forEach((item) => {
        const from = filePositions.get(item.id);
        const to = filePositions.get(item.parent_id);
        if (!from || !to) return;
        // Every item remains a real point and keeps its parent in metadata.
        // Drawing ten thousand simultaneous file edges makes an opaque
        // hairball, so the field shows all folder trunks plus a stable sample
        // of leaves; search and inspection retain the complete hierarchy.
        if (item.kind !== "folder" && hashUnit(`${item.id}:branch`) > edgeBudgetRatio) return;
        branches.push(from.x, from.y, from.z, to.x, to.y, to.z);
        synapseSegments.push([from.clone(), to.clone()]);
      });
      if (branches.length) {
        const branchGeometry = new THREE.BufferGeometry();
        branchGeometry.setAttribute("position", new THREE.Float32BufferAttribute(branches, 3));
        topology.add(new THREE.LineSegments(branchGeometry, new THREE.LineBasicMaterial({
          color: 0x4a9f87, transparent: true, opacity: 0.085,
        })));
      }

      const folderItems = fileItems.filter((item) => item.kind === "folder");
      if (folderItems.length) {
        const folderPositions = new Float32Array(folderItems.length * 3);
        folderItems.forEach((item, index) => {
          const position = filePositions.get(item.id);
          folderPositions.set([position.x, position.y, position.z], index * 3);
        });
        const folderGeometry = new THREE.BufferGeometry();
        folderGeometry.setAttribute("position", new THREE.BufferAttribute(folderPositions, 3));
        topology.add(new THREE.Points(folderGeometry, new THREE.PointsMaterial({
          color: 0xd8fff1, size: 4.6, sizeAttenuation: false, transparent: true,
          opacity: 0.88, blending: THREE.AdditiveBlending, depthWrite: false,
        })));
      }

      buildFileClusters(fileGraph, sourceOrder);

      const state = document.getElementById("brainIndexState");
      const summary = document.getElementById("brainIndexSummary");
      const sourceCount = (fileGraph.sources || []).length;
      if (state) state.textContent = `${total} ${fileGraph.synthetic ? "synthetic" : "authorized"} nodes`;
      if (summary) summary.textContent = ambientSources
        ? `${total} files and folders form a private density field across ${sourceCount} source clusters. ${renderedPointCount < total ? `${renderedPointCount.toLocaleString()} representative nodes are drawn for this device.` : ""} Select a cluster to request its bounded detail.`
        : `${total} files and folders across ${sourceCount} source clusters. Select any node to inspect its provenance.`;
    }

    /* The firing. Each signal walks one synapse and respawns on another. */
    function buildSignals() {
      signalState = [];
      if (!synapseSegments.length) return;
      const count = Math.min(MAX_SIGNALS, synapseSegments.length);
      const positions = new Float32Array(count * 3);
      for (let index = 0; index < count; index += 1) {
        signalState.push(spawnSignal());
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      signals = new THREE.Points(geometry, new THREE.PointsMaterial({
        color: 0x7ef9ff,
        // These are the moving evidence packets. Keep them readable at the
        // console camera distance so a paused topology and a firing topology
        // are visibly different states.
        size: 4,
        sizeAttenuation: false,
        transparent: true,
        opacity: 0.95,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      }));
      topology.add(signals);
    }

    // System nodes are individually selectable meshes. This companion point
    // cloud gives those same nodes a readable, video-like constellation at a
    // distance without inventing decorative data between them.
    function buildArchitectureBeacons() {
      const architecture = meshes.filter((mesh) => mesh.userData.detail?.system);
      if (!architecture.length) return;
      const positions = new Float32Array(architecture.length * 3);
      const palette = new Float32Array(architecture.length * 3);
      const color = new THREE.Color();
      architecture.forEach((mesh, index) => {
        positions.set([mesh.position.x, mesh.position.y, mesh.position.z], index * 3);
        color.setHex(nodeColor(mesh.userData));
        palette.set([color.r, color.g, color.b], index * 3);
      });
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      geometry.setAttribute("color", new THREE.BufferAttribute(palette, 3));
      const beacons = new THREE.Points(geometry, new THREE.PointsMaterial({
        // These are screen-space beacons for the real selectable node meshes.
        // Perspective scaling made the source and model lanes read as dust at
        // the wide console distance; a fixed 9px signal keeps the topology
        // legible without inflating the physical graph geometry.
        size: 9,
        sizeAttenuation: false,
        vertexColors: true,
        transparent: true,
        opacity: 0.96,
        blending: THREE.AdditiveBlending,
        depthTest: false,
        depthWrite: false,
      }));
      beacons.renderOrder = 5;
      topology.add(beacons);
    }

    function spawnSignal() {
      const segment = synapseSegments[Math.floor(Math.random() * synapseSegments.length)];
      return { from: segment[0], to: segment[1], t: Math.random(), speed: 0.25 + Math.random() * 0.5 };
    }

    function advanceSignals(delta) {
      if (!signals || !signalState.length) return;
      const position = signals.geometry.attributes.position;
      for (let index = 0; index < signalState.length; index += 1) {
        const signal = signalState[index];
        if (!motionPaused) {
          signal.t += signal.speed * delta;
          if (signal.t > 1) Object.assign(signal, spawnSignal(), { t: 0 });
        }
        position.setXYZ(
          index,
          signal.from.x + (signal.to.x - signal.from.x) * signal.t,
          signal.from.y + (signal.to.y - signal.from.y) * signal.t,
          signal.from.z + (signal.to.z - signal.from.z) * signal.t,
        );
      }
      position.needsUpdate = true;
    }

    function rebuild(data) {
      snapshot = data;
      const renderSignature = JSON.stringify({
        fileRevision: data.file_graph?.revision || `${data.file_graph?.item_count || 0}:${data.file_graph?.synthetic || false}`,
        lane: root.dataset.activeLane || "claude",
        tasks: data.tasks.map((task) => [task.id, task.status]),
        rules: data.rules.map((rule) => rule.proposal_id),
        brain: data.brain?.items?.map((item) => item.id) || [],
        capabilities: data.capabilities?.items?.map((item) => item.id) || [],
        radar: data.radar?.candidates?.map((item) => `${item.source}:${item.name}`) || [],
      });
      if (renderSignature === lastRenderSignature) return;
      lastRenderSignature = renderSignature;
      clearTopology();
      const nodes = modelFrom(data);
      nodes.forEach((node, index) => {
        const running = node.kind === "task" && node.detail.status === "running";
        const selectedLane = Boolean(node.detail.active);
        const architectureNode = Boolean(node.detail.system);
        const material = new THREE.MeshStandardMaterial({
          color: nodeColor(node),
          emissive: nodeColor(node),
          emissiveIntensity: running || selectedLane ? 1.15 : architectureNode ? 0.72 : 0.34,
          metalness: 0.15,
          roughness: 0.38,
          transparent: true,
          opacity: node.kind === "radar" ? 0.74 : 0.94,
          depthTest: !architectureNode,
          depthWrite: !architectureNode,
        });
        const mesh = new THREE.Mesh(geometryFor(node.kind), material);
        mesh.position.copy(positionFor(node, index, nodes.length));
        mesh.userData = node;
        const baseScale = scaleFor(node.kind);
        mesh.scale.setScalar(baseScale);
        if (architectureNode) mesh.renderOrder = 4;
        topology.add(mesh);
        meshes.push(mesh);
        meshById.set(node.id, mesh);
        if (running || selectedLane || architectureNode) dynamicMaterials.push({ mesh, material, baseScale });
      });
      buildSynapses();
      buildFileUniverse(data.file_graph);
      buildArchitectureBeacons();
      buildSignals();
      const route = (source, target, color = 0x63d9b6, opacity = 0.5) => addEdge(source, target, color, opacity);
      ["obsidian", "drive", "github", "deploys", "sessions", "ingress"].forEach(source => route(`system:${source}`, "system:planner", 0x3b7f6a, 0.34));
      ["claude", "codex", "omniroute", "freellmapi", "ollama"].forEach(model => route("system:planner", `system:model:${model}`, 0x63d9b6, 0.42));
      route("system:model:claude", "system:research", 0x63d9b6, 0.54);
      route("system:model:codex", "system:design", 0x7bbde5, 0.54);
      route("system:model:codex", "system:build", 0x7bbde5, 0.54);
      route("system:model:omniroute", "system:build", 0xe9b86d, 0.48);
      route("system:model:freellmapi", "system:research", 0xe9b86d, 0.48);
      route("system:model:ollama", "system:build", 0x8de0bf, 0.48);
      ["research", "design", "build"].forEach(worker => route(`system:${worker}`, "system:verify", 0xe9b86d, 0.58));
      route("system:verify", "system:brain", 0x63d9b6, 0.58);
      route("system:brain", "system:approval", 0xef9a86, 0.6);
      data.edges.forEach((edge) => addEdge(`task:${edge.parent_id}`, `task:${edge.child_id}`, 0x00f0ff, 0.36));
      data.rule_task_links.forEach((edge) => addEdge(`rule:${edge.proposal_id}`, `task:${edge.task_id}`, 0xffb703, 0.48));
      (data.brain?.edges || []).forEach((edge) => addEdge(`brain:${edge.source_id}`, `brain:${edge.target_id}`, 0xa78bfa, 0.34));
      core.scale.setScalar(1 + Math.min(visualFileNodeCount, 800) / 2400);
      root.dataset.nodes = String(nodes.length + visualFileNodeCount);
    }

    const raycaster = new THREE.Raycaster();
    raycaster.params.Points.threshold = 0.22;
    const pointer = new THREE.Vector2();
    function pick(event) {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      return raycaster.intersectObjects(fileCloud && fileItems.length ? [...meshes, fileCloud] : meshes, false)[0] || null;
    }

    function fileNode(item) {
      if (!item) return null;
      return {
          id: `file:${item.id}`,
          kind: item.kind,
          label: item.label,
          detail: {
            system: true,
            group: `${item.source} / ${item.kind}`,
            description: item.kind === "cluster"
              ? `${item.label} is a private source cluster with ${item.node_count} files and folders. Expand is deliberately bounded so the ambient field does not reveal its file list.`
              : `${item.label} is represented by one provenance-linked node in the authorized index.`,
            context: item.kind === "cluster"
              ? `${item.folder_count || 0} folders recorded; source-scoped detail requires an authenticated expansion.`
              : item.relative_path || (item.synthetic ? "Synthetic demonstration metadata" : "Authorized index metadata"),
            tools: "File graph index and provenance store",
            evidence: item.kind === "cluster"
              ? `Source ${item.source}; cardinality ${item.node_count}; no file metadata loaded.`
              : `Source ${item.source}; parent ${item.parent_id || "source root"}`,
            size: item.size,
            modifiedAt: item.modified_at || item.modified,
            url: item.url || null,
          },
        };
    }

    function nodeFromHit(hit) {
      if (!hit) return null;
      if (hit.object === fileCloud && Number.isInteger(hit.index)) {
        return fileNode(fileItems[hit.index]);
      }
      return hit.object.userData;
    }

    window.addEventListener("tri-ai:file-focus", (event) => {
      const itemId = event.detail?.id;
      const index = fileItems.findIndex((item) => item.id === itemId);
      if (index < 0 || !fileCloud) return;
      const colors = fileCloud.geometry.attributes.color;
      colors.setXYZ(index, 1, 1, 1);
      colors.needsUpdate = true;
      const node = fileNode(fileItems[index]);
      if (node) window.dispatchEvent(new CustomEvent("tri-ai:system-select", { detail: node }));
    });

    function showTooltip(event, hit) {
      const node = nodeFromHit(hit);
      if (!node) {
        tooltip.style.display = "none";
        renderer.domElement.style.cursor = drag ? "grabbing" : "grab";
        return;
      }
      tooltip.replaceChildren();
      const title = document.createElement("b");
      title.textContent = node.label || node.id;
      const detail = document.createElement("span");
      detail.textContent = `${node.detail.system ? node.detail.group : node.kind} // ${node.detail.status || node.detail.availability || node.detail.disposition || "recorded"}`;
      tooltip.append(title, detail);
      tooltip.style.display = "block";
      tooltip.style.left = `${Math.min(root.clientWidth - 250, Math.max(8, event.offsetX + 14))}px`;
      tooltip.style.top = `${Math.max(8, event.offsetY + 14)}px`;
      renderer.domElement.style.cursor = "pointer";
    }

    renderer.domElement.addEventListener("pointerdown", (event) => {
      drag = { x: event.clientX, y: event.clientY, yaw, pitch, moved: false };
      renderer.domElement.setPointerCapture(event.pointerId);
    });
    renderer.domElement.addEventListener("pointermove", (event) => {
      if (drag) {
        const dx = event.clientX - drag.x;
        const dy = event.clientY - drag.y;
        drag.moved ||= Math.hypot(dx, dy) > 4;
        if (drag.moved) {
          yaw = drag.yaw - dx * 0.006;
          pitch = Math.max(-1.15, Math.min(1.15, drag.pitch - dy * 0.005));
          applyCamera();
          tooltip.style.display = "none";
          renderer.domElement.style.cursor = "grabbing";
          return;
        }
      }
      showTooltip(event, pick(event));
    });
    renderer.domElement.addEventListener("pointerup", (event) => {
      const wasDrag = drag?.moved;
      drag = null;
      renderer.domElement.releasePointerCapture?.(event.pointerId);
      if (wasDrag) return;
      const node = nodeFromHit(pick(event));
      if (!node) return;
      selectedId = node.id;
      if (node.detail?.system) {
        window.dispatchEvent(new CustomEvent("tri-ai:system-select", { detail: node }));
      } else {
        window.dispatchEvent(new CustomEvent("tri-ai:select", { detail: { id: selectedId } }));
      }
    });
    renderer.domElement.addEventListener("pointerleave", () => {
      if (!drag) tooltip.style.display = "none";
    });
    renderer.domElement.addEventListener("wheel", (event) => {
      event.preventDefault();
      distance = Math.max(9, Math.min(32, distance + event.deltaY * 0.012));
      applyCamera();
    }, { passive: false });

    function setView(mode) {
      const three = mode === "3d";
      panel.classList.toggle("view-3d", three);
      button3d.classList.toggle("active", three);
      button2d.classList.toggle("active", !three);
      if (three) resize();
      else window.dispatchEvent(new Event("resize"));
    }
    button3d.disabled = false;
    button3d.addEventListener("click", () => setView("3d"));
    button2d.addEventListener("click", () => setView("2d"));
    motionButton.addEventListener("click", () => {
      motionPaused = !motionPaused;
      motionButton.setAttribute("aria-pressed", String(motionPaused));
      motionButton.textContent = motionPaused ? "Resume" : "Pause";
    });
    reducedMotion.addEventListener?.("change", (event) => {
      if (event.matches) {
        motionPaused = true;
        motionButton.setAttribute("aria-pressed", "true");
        motionButton.textContent = "Resume";
      }
    });
    window.addEventListener("tri-ai:snapshot", (event) => rebuild(event.detail));
    window.addEventListener("tri-ai:lane", (event) => {
      root.dataset.activeLane = event.detail?.lane || "claude";
      if (snapshot) rebuild(snapshot);
    });
    new ResizeObserver(resize).observe(root);
    applyCamera();
    // Every width, including a phone. See the header: gating this on viewport
    // width meant the operator had never seen the view at all.
    setView("3d");

    const clock = new THREE.Clock();
    function frame() {
      requestAnimationFrame(frame);
      const delta = clock.getDelta();
      const elapsed = clock.getElapsedTime();
      for (const entry of dynamicMaterials) {
        const pulse = motionPaused ? 1 : 1 + Math.sin(elapsed * 4) * 0.12;
        entry.mesh.scale.setScalar(entry.baseScale * pulse);
        entry.material.emissiveIntensity = motionPaused ? 0.9 : 1.05 + Math.sin(elapsed * 4) * 0.3;
      }
      advanceSignals(delta);
      if (!motionPaused) {
        // The whole organ turns, not just its core - it should read as one
        // floating body rather than a still cloud with a spinning centre.
        const breath = 1 + Math.sin(elapsed * 0.72) * 0.008;
        topology.scale.setScalar(breath);
        topology.rotation.y = elapsed * 0.042;
        core.rotation.y = elapsed * 0.11;
      }
      renderer.render(scene, camera);
    }
    frame();
  } catch (error) {
    console.warn("Tri-AI spatial view unavailable; retaining the 2D topology.", error);
    button3d.disabled = true;
  }
}
