// The 3D parts of the website: Flash next to a laptop (hero) and the sandbox (safety).
// Everything is built from simple shapes in code - no model files, nothing loaded from other servers.
import * as THREE from "./vendor/three.module.min.js";

const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const night = document.body.classList.contains("night");
const ACCENT = 0xd97757;
const FUR = night ? 0x2b2b33 : 0x1a1a1e;   // Flash's coat - change it here
const MONO = '"SF Mono", ui-monospace, Menlo, Monaco, monospace';
const pointer = { x: 0, y: 0 };            // -1 … 1 across the window
addEventListener("pointermove", e => {
  pointer.x = e.clientX / innerWidth * 2 - 1;
  pointer.y = e.clientY / innerHeight * 2 - 1;
});
const rand = (a, b) => a + Math.random() * (b - a);
const ease = (from, to, k) => from + (to - from) * k;

// ---------- one canvas: renderer, camera, and a loop that only runs while it is visible ----------
function stage(el, fov, build) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(fov, 1, 0.1, 100);
  const world = build(scene, camera, renderer);
  el.appendChild(renderer.domElement);
  let visible = true, running = false, last = 0;
  const draw = now => {
    const t = now / 1000;
    world.update(t, Math.min(0.05, t - last || 0.016));
    last = t;
    renderer.render(scene, camera);
  };
  const loop = now => {
    if (!visible || document.hidden) { running = false; return; }
    draw(now);
    requestAnimationFrame(loop);
  };
  const start = () => { if (!running && !reduced) { running = true; requestAnimationFrame(loop); } };
  const resize = () => {
    const w = el.clientWidth, h = el.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    world.frame && world.frame(camera.aspect);
    camera.updateProjectionMatrix();
    if (reduced || !running) draw(performance.now());
  };
  new ResizeObserver(resize).observe(el);
  new IntersectionObserver(es => { visible = es[0].isIntersecting; start(); }).observe(el);
  document.addEventListener("visibilitychange", start);
  resize();
  start();
  return { renderer, scene, camera, draw: () => draw(performance.now()) };
}

function lights(scene) {
  scene.add(new THREE.HemisphereLight(night ? 0x8fa3ff : 0xfff1e6, night ? 0x0c0c12 : 0x8a4630, night ? 0.9 : 2.2));
  const key = new THREE.DirectionalLight(night ? 0xaab8ff : 0xffffff, night ? 1.4 : 3.2);
  key.position.set(2.5, 5, 3.5);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  Object.assign(key.shadow.camera, { left: -4, right: 4, top: 4, bottom: -4, near: 0.5, far: 14 });
  key.shadow.bias = -0.0005;
  key.shadow.radius = 6;
  scene.add(key);
  const rim = new THREE.PointLight(ACCENT, night ? 40 : 18, 12);
  rim.position.set(-2.5, 2.2, -2.5);
  scene.add(rim);
  return key;
}

// a small picture drawn in code, used as a texture
function picture(w, h, paint) {
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  const g = c.getContext("2d");
  paint(g, w, h);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}

// a file as a small slab with its name on it
function tile(label, { fg = "#d97757", bg = "#1c1d21", edge = "#3a393b", mark = "", glow = night ? 0.7 : 0.25 } = {}) {
  const face = picture(256, 320, (g, w, h) => {
    g.fillStyle = bg; g.fillRect(0, 0, w, h);
    g.strokeStyle = edge; g.lineWidth = 10; g.strokeRect(5, 5, w - 10, h - 10);
    g.fillStyle = edge;                                   // folded corner
    g.beginPath(); g.moveTo(w - 70, 0); g.lineTo(w, 70); g.lineTo(w, 0); g.fill();
    g.fillStyle = "#3a393b";
    for (let i = 0; i < 3; i++) g.fillRect(34, 60 + i * 30, [130, 100, 150][i], 10);
    g.fillStyle = fg; g.textAlign = "center"; g.textBaseline = "middle";
    g.font = `800 ${label.length > 5 ? 44 : 68}px ${MONO}`;
    g.fillText(label, w / 2, h * 0.64);
    if (mark) { g.font = `800 54px ${MONO}`; g.fillText(mark, w / 2, h * 0.87); }
  });
  const side = new THREE.MeshStandardMaterial({ color: 0x232327, roughness: 0.6 });
  const front = new THREE.MeshStandardMaterial({ map: face, roughness: 0.55, emissive: 0xffffff, emissiveMap: face, emissiveIntensity: glow });
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(0.4, 0.5, 0.04), [side, side, side, side, front, front]);
  return mesh;
}

// ---------- Flash ----------
function buildCat() {
  const cat = new THREE.Group();
  const fur = new THREE.MeshStandardMaterial({ color: FUR, roughness: 0.72 });
  const add = (parent, geo, mat, pos, scale) => {
    const m = new THREE.Mesh(geo, mat);
    m.position.set(...pos);
    if (scale) m.scale.set(...scale);
    m.castShadow = true;
    parent.add(m);
    return m;
  };
  // body: a pear, turned on the lathe
  const outline = new THREE.SplineCurve([[0.001, 0], [0.4, 0.02], [0.5, 0.3], [0.43, 0.68], [0.29, 0.98], [0.001, 1.06]]
    .map(p => new THREE.Vector2(...p))).getPoints(28);
  const body = add(cat, new THREE.LatheGeometry(outline, 40), fur, [0, 0, 0], [1, 1, 1.08]);
  const ball = new THREE.SphereGeometry(1, 28, 20);
  for (const s of [-1, 1]) {
    add(cat, ball, fur, [0.36 * s, 0.25, -0.02], [0.2, 0.26, 0.32]);                       // haunch
    add(cat, new THREE.CapsuleGeometry(0.085, 0.42, 6, 14), fur, [0.17 * s, 0.33, 0.5]);   // front leg
    add(cat, ball, fur, [0.17 * s, 0.06, 0.58], [0.11, 0.065, 0.15]);                      // front paw
    add(cat, ball, fur, [0.4 * s, 0.05, 0.3], [0.1, 0.06, 0.17]);                          // hind paw
  }

  const head = new THREE.Group();
  head.position.set(0, 1.2, 0.14);
  head.scale.setScalar(1.18);
  cat.add(head);
  add(head, ball, fur, [0, 0, 0], [0.37, 0.32, 0.33]);
  const pink = new THREE.MeshStandardMaterial({ color: 0xe9a08a, roughness: 0.6 });
  const ears = [];
  for (const s of [-1, 1]) {
    const ear = new THREE.Group();
    ear.position.set(0.2 * s, 0.24, 0);
    ear.rotation.z = -0.3 * s;
    add(ear, new THREE.ConeGeometry(0.14, 0.3, 20), fur, [0, 0.1, 0], [1, 1, 0.55]);
    add(ear, new THREE.ConeGeometry(0.085, 0.2, 20), pink, [0, 0.08, 0.035], [1, 1, 0.4]);
    head.add(ear);
    ears.push(ear);
    add(head, ball, fur, [0.085 * s, -0.1, 0.27], [0.1, 0.075, 0.08]);                     // cheek
  }
  add(head, ball, pink, [0, -0.055, 0.34], [0.035, 0.026, 0.025]);                         // nose
  const iris = new THREE.MeshStandardMaterial({ color: ACCENT, emissive: ACCENT, emissiveIntensity: night ? 1.2 : 0.55, roughness: 0.3 });
  const black = new THREE.MeshBasicMaterial({ color: 0x050505 });
  const eyes = [], pupils = [];
  for (const s of [-1, 1]) {
    const eye = new THREE.Group();
    eye.position.set(0.14 * s, 0.045, 0.27);
    add(eye, ball, iris, [0, 0, 0], [0.078, 0.078, 0.05]).castShadow = false;
    pupils.push(add(eye, ball, black, [0, 0, 0.04], [0.026, 0.06, 0.02]));
    head.add(eye);
    eyes.push(eye);
  }
  const hairs = [];
  for (const s of [-1, 1]) for (const dy of [-0.05, 0.01, 0.07])
    hairs.push(0.11 * s, -0.1, 0.33, 0.47 * s, -0.1 - dy * 1.6, 0.3 - Math.abs(dy));
  const whiskers = new THREE.LineSegments(new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(hairs, 3)),
    new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.55 }));
  head.add(whiskers);

  // tail: a chain of short pieces, each one bends a little more
  const tail = [];
  let parent = cat;
  for (let i = 0; i < 10; i++) {
    const joint = new THREE.Group();
    joint.position.set(0, i ? 0.15 : 0.14, i ? 0 : -0.44);
    const r = 0.075 - i * 0.003;
    add(joint, new THREE.CapsuleGeometry(r, 0.15, 4, 10), fur, [0, 0.075, 0]);
    parent.add(joint);
    parent = joint;
    tail.push(joint);
  }

  const state = { yaw: 0, pitch: 0, lid: 1, blinkAt: 2, blinkUntil: 0, purrUntil: 0, twitchAt: 3, twitch: -1 };
  cat.userData.purr = t => { state.purrUntil = t + 1.6; };
  cat.userData.update = t => {
    const purring = t < state.purrUntil;
    const breath = Math.sin(t * (night ? 1.1 : 1.9));
    body.scale.set(1 + breath * 0.012, 1 + breath * 0.008, 1.08 + breath * 0.02);
    // head: follows the pointer by day, hangs low at night
    const yaw = night ? 0.25 : THREE.MathUtils.clamp(pointer.x * 0.9, -0.75, 0.75);
    const pitch = night ? 0.5 : THREE.MathUtils.clamp(pointer.y * 0.45, -0.3, 0.4);
    state.yaw = ease(state.yaw, yaw, 0.08);
    state.pitch = ease(state.pitch, pitch, 0.08);
    head.rotation.set(state.pitch, state.yaw, purring ? Math.sin(t * 3) * 0.12 : 0);
    head.position.y = 1.2 + breath * 0.008 - (night ? 0.06 : 0);
    cat.position.x = cat.userData.x + (purring ? Math.sin(t * 70) * 0.004 : 0);
    // eyes
    if (t > state.blinkAt) { state.blinkUntil = t + 0.13; state.blinkAt = t + rand(2.2, 6); }
    const lid = night ? 0.07 : purring ? 0.28 : t < state.blinkUntil ? 0.08 : 1;
    state.lid = ease(state.lid, lid, 0.35);
    for (const [i, eye] of eyes.entries()) {
      eye.scale.y = state.lid;
      pupils[i].position.x = THREE.MathUtils.clamp((pointer.x - state.yaw) * 0.02, -0.022, 0.022);
      pupils[i].scale.x = purring ? 0.05 : 0.026;
    }
    // ears twitch now and then
    if (t > state.twitchAt) { state.twitch = Math.random() < 0.5 ? 0 : 1; state.twitchAt = t + rand(3, 8); state.twitchUntil = t + 0.25; }
    ears.forEach((ear, i) => { ear.rotation.x = i === state.twitch && t < state.twitchUntil ? Math.sin((state.twitchUntil - t) * 50) * 0.18 : 0; });
    // tail
    const speed = purring ? 5 : night ? 0.6 : 1.8;
    tail.forEach((joint, i) => {
      joint.rotation.x = i === 0 ? -0.75 : 0.02 + i * 0.045;
      joint.rotation.z = Math.sin(t * speed - i * 0.55) * (0.05 + i * 0.022);
    });
  };
  return cat;
}

// ---------- the laptop with a terminal that types ----------
const SCREEN = [
  ["cat", "   /\\_/\\"], ["cat", night ? "  ( -.- )" : "  ( o.o )"], ["cat", "   > ^ <"], ["", ""],
  ["type", "which invoice is due first?"], ["", ""],
  ["tool", "  ◇ looks into the folder", "✓"],
  ["tool", "  ◇ reads blue-harbor-hosting.pdf", "✓"],
  ["tool", "  ◇ reads northwind-design.pdf", "✓"], ["", ""],
  ["text", "⏺ BH-55710 from Blue Harbor Hosting,"],
  ["text", "  due on September 29, 2026."], ["", ""],
];
function buildLaptop() {
  const laptop = new THREE.Group();
  const metal = new THREE.MeshStandardMaterial({ color: night ? 0x9a9da6 : 0xd5d7dc, metalness: 0.7, roughness: 0.32 });
  const dark = new THREE.MeshStandardMaterial({ color: 0x18181b, roughness: 0.6 });
  const base = new THREE.Mesh(new THREE.BoxGeometry(1.8, 0.05, 1.2), metal);
  base.position.y = 0.025;
  base.castShadow = base.receiveShadow = true;
  laptop.add(base);
  const keys = new THREE.Mesh(new THREE.BoxGeometry(1.56, 0.012, 0.56), dark);
  keys.position.set(0, 0.052, -0.17);
  const pad = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.006, 0.34), new THREE.MeshStandardMaterial({ color: night ? 0x8a8d96 : 0xc3c5ca, metalness: 0.6, roughness: 0.4 }));
  pad.position.set(0, 0.051, 0.37);
  laptop.add(keys, pad);
  const lid = new THREE.Group();
  lid.position.set(0, 0.05, -0.6);
  lid.rotation.x = -0.3;
  const back = new THREE.Mesh(new THREE.BoxGeometry(1.8, 1.16, 0.035), metal);
  back.position.y = 0.58;
  back.castShadow = true;
  const bezel = new THREE.Mesh(new THREE.PlaneGeometry(1.76, 1.12), new THREE.MeshBasicMaterial({ color: 0x08080a }));
  bezel.position.set(0, 0.58, 0.0185);

  const W = 760, H = 475, SIZE = 23, STEP = 29;
  const canvas = document.createElement("canvas");
  canvas.width = W; canvas.height = H;
  const g = canvas.getContext("2d");
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  const colors = { cat: "#a8a29d", tool: "#a8a29d", text: "#ececec", type: "#ececec" };
  const paint = (lines, typed, cursor) => {
    g.fillStyle = "#1c1d21"; g.fillRect(0, 0, W, H);
    ["#ff5f57", "#febc2e", "#28c840"].forEach((c, i) => { g.fillStyle = c; g.beginPath(); g.arc(26 + i * 24, 24, 8, 0, 7); g.fill(); });
    g.font = `${SIZE}px ${MONO}`; g.textBaseline = "top";
    let y = 52;
    const prompt = (text, on) => {
      g.fillStyle = "#d97757"; g.fillText("❯ ", 28, y);
      const x = 28 + g.measureText("❯ ").width;
      g.fillStyle = "#ececec"; g.fillText(text, x, y);
      if (on) g.fillRect(x + g.measureText(text).width + 3, y + 1, 12, SIZE);
    };
    for (let i = 0; i < lines; i++) {
      const [kind, text, ok] = SCREEN[i];
      if (kind === "type") prompt(i === lines - 1 ? text.slice(0, typed) : text, i === lines - 1 && cursor);
      else if (kind) {
        g.fillStyle = colors[kind]; g.fillText(text, 28, y);
        if (kind === "text" && text.startsWith("⏺")) { g.fillStyle = "#61afef"; g.fillText("⏺", 28, y); }
        if (ok) { g.fillStyle = "#98c379"; g.fillText(ok, 28 + g.measureText("  ◇ reads blue-harbor-hosting.pdf   ").width, y); }
      }
      y += STEP;
    }
    if (lines === SCREEN.length) prompt("", cursor);
    tex.needsUpdate = true;
  };
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(1.68, 1.05), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false }));
  screen.position.set(0, 0.58, 0.02);
  lid.add(back, bezel, screen);
  laptop.add(lid);
  const glow = new THREE.PointLight(0xbfd4ff, night ? 6 : 2, 4);
  glow.position.set(0, 0.7, 0.5);
  laptop.add(glow);

  // the film: cat, the question typed letter by letter, then line by line; after a pause it starts again
  const typeAt = SCREEN.findIndex(l => l[0] === "type"), question = SCREEN[typeAt][1].length;
  let shown = "";
  laptop.userData.update = t => {
    if (reduced) { if (!shown) { shown = "all"; paint(SCREEN.length, question, true); } return; }
    const clock = t % 16;
    let lines, typed = question;
    if (clock < 1.2) { lines = typeAt + 1; typed = 0; }
    else if (clock < 1.2 + question * 0.07) { lines = typeAt + 1; typed = Math.floor((clock - 1.2) / 0.07); }
    else lines = Math.min(SCREEN.length, typeAt + 1 + Math.floor((clock - 1.2 - question * 0.07) / 0.45));
    const cursor = Math.floor(t * 1.8) % 2 === 0;
    const key = `${lines}/${typed}/${cursor}`;
    if (key !== shown) { shown = key; paint(lines, typed, cursor); }
  };
  return laptop;
}

// ---------- hero: Flash, his laptop and the files he reads ----------
function hero(el) {
  let cat, raycaster = new THREE.Raycaster();
  const s = stage(el, 30, (scene, camera) => {
    lights(scene);
    const world = new THREE.Group();
    scene.add(world);
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(30, 30), new THREE.ShadowMaterial({ opacity: night ? 0.45 : 0.28 }));
    ground.rotation.x = -Math.PI / 2;
    ground.receiveShadow = true;
    world.add(ground);

    const laptop = buildLaptop();
    laptop.position.set(-1.0, 0, 0);
    laptop.scale.setScalar(1.12);
    laptop.rotation.y = 0.42;
    cat = buildCat();
    cat.userData.x = 1.2;
    cat.position.set(1.2, 0, 0.3);
    cat.rotation.y = -0.45;
    world.add(laptop, cat);

    const files = [[".md", -2.0, 1.55, -0.9], ["PDF", -0.75, 2.2, -1.1], ["JPG", 0.35, 2.05, -1.3], ["</>", 1.4, 2.35, -1.0], ["WWW", 2.25, 1.6, -0.7]]
      .map(([label, x, y, z], i) => {
        const f = tile(label);
        f.position.set(x, y, z);
        f.userData = { y, phase: i * 1.3 };
        world.add(f);
        return f;
      });
    const zs = [];
    if (night) for (let i = 0; i < 3; i++) {
      const z = new THREE.Sprite(new THREE.SpriteMaterial({ transparent: true, map: picture(64, 64, (g) => {
        g.fillStyle = "#a8a29d"; g.font = `800 52px ${MONO}`; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(i === 2 ? "Z" : "z", 32, 34);
      }) }));
      world.add(z);
      zs.push(z);
    }
    let scroll = 0;
    return {
      frame: aspect => {
        // narrow windows: step back until laptop and cat both fit
        const d = 6.4 * Math.max(1, 1.45 / aspect);
        camera.position.set(0, 1.55 + (d - 6.4) * 0.12, d);
        camera.userData.d = d;
      },
      update: t => {
        laptop.userData.update(t);
        cat.userData.update(t);
        for (const f of files) {
          f.position.y = f.userData.y + Math.sin(t * 0.9 + f.userData.phase) * 0.07;
          f.rotation.set(Math.sin(t * 0.6 + f.userData.phase) * 0.12, Math.sin(t * 0.45 + f.userData.phase) * 0.35, 0);
        }
        zs.forEach((z, i) => {
          const k = (t * 0.33 + i / 3) % 1;
          z.position.set(1.45 + k * 0.5, 1.5 + k * 0.9, 0.5);
          z.scale.setScalar(0.14 + k * 0.14);
          z.material.opacity = Math.sin(k * Math.PI) * 0.9;
        });
        scroll = ease(scroll, Math.min(1, scrollY / (innerHeight || 1)), 0.1);
        world.rotation.y = pointer.x * -0.06 + scroll * 0.5;
        camera.position.x = ease(camera.position.x, pointer.x * 0.25, 0.05);
        camera.lookAt(0, 0.95, 0);
      },
    };
  });
  const hit = e => {
    const r = el.getBoundingClientRect();
    raycaster.setFromCamera(new THREE.Vector2((e.clientX - r.left) / r.width * 2 - 1, -(e.clientY - r.top) / r.height * 2 + 1), s.camera);
    return raycaster.intersectObject(cat, true).length > 0;
  };
  const purr = (x, y) => {
    cat.userData.purr(performance.now() / 1000);
    if (reduced) s.draw();
    dispatchEvent(new CustomEvent("flash-purr", { detail: { x, y } }));
  };
  el.addEventListener("pointermove", e => { el.style.cursor = hit(e) ? "pointer" : ""; });
  el.addEventListener("click", e => { if (hit(e)) { e.stopPropagation(); purr(e.clientX, e.clientY); } });
  const button = document.getElementById("pet");
  if (button) button.addEventListener("click", e => {
    e.stopPropagation();
    const r = el.getBoundingClientRect();
    purr(r.left + r.width * 0.68, r.top + r.height * 0.4);
  });
}

// ---------- safety: commands stay in their box ----------
function sandbox(el) {
  stage(el, 32, (scene, camera) => {
    lights(scene);
    const world = new THREE.Group();
    scene.add(world);
    const SIZE = 1.9, HALF = SIZE / 2;
    const cube = new THREE.BoxGeometry(SIZE, SIZE, SIZE);
    const glass = new THREE.Mesh(cube, new THREE.MeshBasicMaterial({ color: ACCENT, transparent: true, opacity: 0.06, depthWrite: false, side: THREE.DoubleSide }));
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(cube), new THREE.LineBasicMaterial({ color: ACCENT }));
    world.add(glass, edges);
    // inside: the start folder's files
    const inside = [["notes.md", -0.42, 0.3, 0.1], ["PDF", 0.42, -0.05, -0.3], ["app.py", -0.05, -0.48, 0.35]].map(([label, x, y, z], i) => {
      const f = tile(label, { fg: "#98c379", edge: "#4c6b3c", mark: "✓", glow: 0.9 });
      f.scale.setScalar(1.05);
      f.position.set(x, y, z);
      f.userData = { y, phase: i * 2 };
      world.add(f);
      return f;
    });
    // outside: what is locked
    const outside = ["Library", ".ssh", ".zshrc"].map((label, i) => {
      const f = tile(label, { fg: "#9a948f", edge: "#4a4850", bg: "#222226", mark: "✕", glow: 0.75 });
      f.scale.setScalar(0.95);
      f.userData = { angle: i * Math.PI * 2 / 3, y: [0.5, -0.45, 0.05][i] };
      scene.add(f);
      return f;
    });
    // a command: bounces around and never gets out
    const ball = new THREE.Mesh(new THREE.SphereGeometry(0.09, 24, 16), new THREE.MeshStandardMaterial({ color: 0xffffff, emissive: ACCENT, emissiveIntensity: 1.6 }));
    const lamp = new THREE.PointLight(ACCENT, 3, 2.5);
    ball.add(lamp);
    world.add(ball);
    const v = new THREE.Vector3(0.9, 0.62, 0.75);
    const limit = HALF - 0.09;
    let flash = 0;
    return {
      frame: aspect => { camera.position.set(0, 0.45, 5.6 * Math.max(1, 1.5 / aspect)); },
      update: (t, dt) => {
        ball.position.addScaledVector(v, dt);
        for (const axis of ["x", "y", "z"]) {
          if (Math.abs(ball.position[axis]) > limit) {
            ball.position[axis] = Math.sign(ball.position[axis]) * limit;
            v[axis] *= -1;
            flash = 1;
          }
        }
        flash = Math.max(0, flash - dt * 2.5);
        glass.material.opacity = 0.06 + flash * 0.14;
        for (const f of inside) {
          f.position.y = f.userData.y + Math.sin(t + f.userData.phase) * 0.05;
          f.rotation.y = Math.sin(t * 0.5 + f.userData.phase) * 0.5 - world.rotation.y;
        }
        world.rotation.y = t * 0.22 + pointer.x * 0.3;
        world.rotation.x = 0.18 + pointer.y * 0.1;
        for (const f of outside) {
          const a = f.userData.angle + t * 0.16;
          f.position.set(Math.cos(a) * 2.05, f.userData.y + Math.sin(t * 0.7 + a) * 0.08, Math.sin(a) * 1.7);
          f.rotation.y = Math.sin(t * 0.4 + a) * 0.3;
        }
        camera.lookAt(0, 0, 0);
      },
    };
  });
}

try {
  const heroEl = document.getElementById("stage");
  const boxEl = document.getElementById("sandbox");
  if (heroEl) hero(heroEl);
  if (boxEl) sandbox(boxEl);
  document.documentElement.classList.add("three");   // hides the drawn stand-ins
} catch (e) {
  console.warn("3D is not available, the page shows its drawn cats instead:", e);
}
