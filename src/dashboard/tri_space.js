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
const MAX_SIGNALS = 48;
const NEIGHBOURS_PER_NODE = 2;
const MEMBRANE_DETAIL = 3;

// How far each hemisphere is pushed off the midline. The gap is the single
// feature that makes the silhouette read as a brain rather than as a ball.
const FISSURE = 0.13;

// Depth of the surface folding. Enough to break the sphere, little enough
// that node positions stay readable.
const GYRI = 0.06;

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
      // Longer front to back than it is wide, and flatter than it is long.
      p.x *= 1.26;
      p.y *= 0.84;
      p.z *= 1.02;
      // Split the hemispheres: every point moves away from the midline, so a
      // valley opens along it instead of a seam through a solid ball.
      const side = p.z >= 0 ? 1 : -1;
      p.z = side * (Math.abs(p.z) * 0.84 + FISSURE);
      // Fold the surface. Two frequencies, so the folds do not repeat
      // regularly enough to read as a pattern.
      const fold = 1
        + GYRI * Math.sin(p.x * 5.2) * Math.cos(p.z * 4.6)
        + GYRI * 0.6 * Math.sin(p.y * 6.1 + p.x * 2.0);
      p.multiplyScalar(fold);
      // Taper the frontal pole. A brain is not symmetric front to back, and
      // the asymmetry is most of what makes it recognisable in outline.
      if (p.x < 0) p.multiplyScalar(1 + p.x * 0.05);
      return p;
    }

    const core = new THREE.Mesh(
      new THREE.IcosahedronGeometry(0.7, 2),
      new THREE.MeshBasicMaterial({ color: 0x00f0ff, wireframe: true, transparent: true, opacity: 0.35 }),
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
        color: 0x1d7f9c, wireframe: true, transparent: true, opacity: 0.16,
      }));
      // Survives a rebuild. The shell is the form itself, not contents, and
      // recomputing 1,280 deformed vertices on every snapshot would be waste.
      mesh.userData.membrane = true;
      return mesh;
    }
    topology.add(buildMembrane(7.1));

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
    let yaw = 0.42;
    let pitch = 0.18;
    let distance = 20.5;
    let drag = null;

    const colors = {
      task: 0x00f0ff,
      rule: 0xffb703,
      brain: 0xa78bfa,
      capability: 0x38bdf8,
      radar: 0xf472b6,
      done: 0x00ff9d,
      failed: 0xff4d6d,
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
      return new THREE.SphereGeometry(0.4, 16, 10);
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
    }

    function modelFrom(data) {
      const brain = data.brain || { items: [] };
      const capabilities = data.capabilities || { items: [] };
      const radar = data.radar || { candidates: [] };
      return [
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
        color: 0x2de2e6, transparent: true, opacity: 0.13,
      }));
      topology.add(synapses);
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
        size: 0.26,
        transparent: true,
        opacity: 0.95,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      }));
      topology.add(signals);
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
      clearTopology();
      const nodes = modelFrom(data);
      nodes.forEach((node, index) => {
        const running = node.kind === "task" && node.detail.status === "running";
        const material = new THREE.MeshStandardMaterial({
          color: nodeColor(node),
          emissive: nodeColor(node),
          emissiveIntensity: running ? 1.15 : 0.34,
          metalness: 0.15,
          roughness: 0.38,
          transparent: true,
          opacity: node.kind === "radar" ? 0.74 : 0.94,
        });
        const mesh = new THREE.Mesh(geometryFor(node.kind), material);
        mesh.position.copy(positionFor(node, index, nodes.length));
        mesh.userData = node;
        topology.add(mesh);
        meshes.push(mesh);
        meshById.set(node.id, mesh);
        if (running) dynamicMaterials.push({ mesh, material });
      });
      buildSynapses();
      buildSignals();
      data.edges.forEach((edge) => addEdge(`task:${edge.parent_id}`, `task:${edge.child_id}`, 0x00f0ff, 0.36));
      data.rule_task_links.forEach((edge) => addEdge(`rule:${edge.proposal_id}`, `task:${edge.task_id}`, 0xffb703, 0.48));
      (data.brain?.edges || []).forEach((edge) => addEdge(`brain:${edge.source_id}`, `brain:${edge.target_id}`, 0xa78bfa, 0.34));
      core.scale.setScalar(1 + Math.min(nodes.length, 80) / 180);
      root.dataset.nodes = String(nodes.length);
    }

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    function pick(event) {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      return raycaster.intersectObjects(meshes, false)[0]?.object || null;
    }

    function showTooltip(event, mesh) {
      if (!mesh) {
        tooltip.style.display = "none";
        renderer.domElement.style.cursor = drag ? "grabbing" : "grab";
        return;
      }
      const node = mesh.userData;
      tooltip.replaceChildren();
      const title = document.createElement("b");
      title.textContent = node.label || node.id;
      const detail = document.createElement("span");
      detail.textContent = `${node.kind} // ${node.detail.status || node.detail.availability || node.detail.disposition || "recorded"}`;
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
      const mesh = pick(event);
      if (!mesh) return;
      selectedId = mesh.userData.id;
      window.dispatchEvent(new CustomEvent("tri-ai:select", { detail: { id: selectedId } }));
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
        entry.mesh.scale.setScalar(pulse);
        entry.material.emissiveIntensity = motionPaused ? 0.9 : 1.05 + Math.sin(elapsed * 4) * 0.3;
      }
      advanceSignals(delta);
      if (!motionPaused) {
        // The whole organ turns, not just its core - it should read as one
        // floating body rather than a still cloud with a spinning centre.
        topology.rotation.y = elapsed * 0.055;
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
