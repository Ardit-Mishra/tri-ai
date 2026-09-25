import * as THREE from "/assets/three.module.min.js";

const root = document.getElementById("spatialGraph");
const panel = root && root.closest(".graph-panel");
const button3d = document.getElementById("graph3d");
const button2d = document.getElementById("graph2d");
const motionButton = document.getElementById("motionToggle");
const tooltip = document.getElementById("spatialTooltip");

if (root && panel && button3d && button2d && motionButton && tooltip) {
  try {
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.setClearColor(0x05070a, 0);
    root.prepend(renderer.domElement);

    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x05070a, 0.035);
    const camera = new THREE.PerspectiveCamera(44, 1, 0.1, 100);
    const topology = new THREE.Group();
    scene.add(topology);
    const guides = new THREE.Group();
    scene.add(guides);
    scene.add(new THREE.AmbientLight(0x9ddcff, 1.1));
    const keyLight = new THREE.PointLight(0x00f0ff, 45, 45);
    keyLight.position.set(4, 6, 8);
    scene.add(keyLight);
    const warmLight = new THREE.PointLight(0xffb703, 18, 32);
    warmLight.position.set(-8, -3, 4);
    scene.add(warmLight);

    const core = new THREE.Mesh(
      new THREE.IcosahedronGeometry(0.86, 2),
      new THREE.MeshBasicMaterial({ color: 0x00f0ff, wireframe: true, transparent: true, opacity: 0.6 }),
    );
    topology.add(core);

    for (const [radius, color] of [[3.25, 0xa78bfa], [4.7, 0x00f0ff], [5.9, 0xffb703], [7.2, 0x38bdf8], [8.5, 0xf472b6]]) {
      const points = [];
      for (let index = 0; index < 96; index += 1) {
        const angle = index / 96 * Math.PI * 2;
        points.push(new THREE.Vector3(Math.cos(angle) * radius, 0, Math.sin(angle) * radius));
      }
      const geometry = new THREE.BufferGeometry().setFromPoints(points);
      const material = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.09 });
      guides.add(new THREE.LineLoop(geometry, material));
    }

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let motionPaused = reducedMotion.matches;
    let snapshot = null;
    let selectedId = null;
    let meshes = [];
    let meshById = new Map();
    let dynamicMaterials = [];
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

    function positionFor(node, index, total) {
      const shell = { task: 4.7, brain: 3.25, rule: 5.9, capability: 7.2, radar: 8.5 }[node.kind] || 5;
      const seed = hashUnit(node.id);
      const y = 1 - 2 * ((index + seed) / Math.max(total, 1) % 1);
      const radial = Math.sqrt(Math.max(0.08, 1 - y * y));
      const angle = Math.PI * 2 * ((index * 0.61803398875 + seed) % 1);
      return new THREE.Vector3(
        Math.cos(angle) * radial * shell,
        y * shell,
        Math.sin(angle) * radial * shell,
      );
    }

    function geometryFor(kind) {
      if (kind === "rule") return new THREE.BoxGeometry(0.52, 0.52, 0.52);
      if (kind === "brain") return new THREE.IcosahedronGeometry(0.43, 1);
      if (kind === "capability") return new THREE.TetrahedronGeometry(0.41, 0);
      if (kind === "radar") return new THREE.OctahedronGeometry(0.37, 0);
      return new THREE.SphereGeometry(0.45, 18, 12);
    }

    function nodeColor(node) {
      if (node.kind === "task") {
        if (node.detail.status === "done") return colors.done;
        if (node.detail.status === "failed" || node.detail.status === "cancelled") return colors.failed;
      }
      return colors[node.kind] || colors.task;
    }

    function clearTopology() {
      for (const child of [...topology.children]) {
        if (child === core) continue;
        topology.remove(child);
        child.geometry?.dispose();
        if (Array.isArray(child.material)) child.material.forEach((material) => material.dispose());
        else child.material?.dispose();
      }
      meshes = [];
      meshById = new Map();
      dynamicMaterials = [];
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

    function addCoreTrace(mesh, color) {
      const geometry = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), mesh.position]);
      const material = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.12 });
      topology.add(new THREE.Line(geometry, material));
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
        addCoreTrace(mesh, nodeColor(node));
        if (running) dynamicMaterials.push({ mesh, material });
      });
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
    setView(window.innerWidth > 767 ? "3d" : "2d");

    const clock = new THREE.Clock();
    function frame() {
      requestAnimationFrame(frame);
      const elapsed = clock.getElapsedTime();
      for (const entry of dynamicMaterials) {
        const pulse = motionPaused ? 1 : 1 + Math.sin(elapsed * 4) * 0.12;
        entry.mesh.scale.setScalar(pulse);
        entry.material.emissiveIntensity = motionPaused ? 0.9 : 1.05 + Math.sin(elapsed * 4) * 0.3;
      }
      core.rotation.y = motionPaused ? 0 : elapsed * 0.08;
      renderer.render(scene, camera);
    }
    frame();
  } catch (error) {
    console.warn("Tri-AI spatial view unavailable; retaining the 2D topology.", error);
    button3d.disabled = true;
  }
}
