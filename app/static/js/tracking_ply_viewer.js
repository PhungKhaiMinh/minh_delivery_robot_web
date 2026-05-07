/**
 * Three.js viewer cho point cloud PLY (Tracking).
 * Giữ tọa độ x,y,z như file PLY; robot MQTT (x,y) tại (x, y, zPlane), zPlane gần zmin.
 */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

/**
 * @param {HTMLElement} container
 * @param {{ bounds: Record<string, number>, positions: Float32Array }} data
 */
export function createPlyMapViewer(container, data) {
    const bounds = data.bounds;
    const positions = data.positions;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x343434);

    const camera = new THREE.PerspectiveCamera(50, 1, 0.02, 1e6);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    container.appendChild(renderer.domElement);

    const geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const mat = new THREE.PointsMaterial({
        color: 0xb4c2d9,
        size: 0.08,
        sizeAttenuation: true,
        opacity: 0.9,
        transparent: true,
    });
    const points = new THREE.Points(geom, mat);
    scene.add(points);

    const cx = (bounds.xmin + bounds.xmax) / 2;
    const cy = (bounds.ymin + bounds.ymax) / 2;
    const cz = (bounds.zmin + bounds.zmax) / 2;
    const dx = Math.max(1e-6, bounds.xmax - bounds.xmin);
    const dy = Math.max(1e-6, bounds.ymax - bounds.ymin);
    const dz = Math.max(1e-6, bounds.zmax - bounds.zmin);
    const span = Math.sqrt(dx * dx + dy * dy + dz * dz);
    const zPlane = bounds.zmin + dz * 0.02;

    const camDist = span * 0.85;
    camera.position.set(cx + camDist * 0.55, cy + camDist * 0.35, cz + camDist * 0.55);
    camera.near = Math.max(0.01, span / 5000);
    camera.far = Math.max(span * 30, 500);
    camera.lookAt(cx, cy, cz);
    camera.updateProjectionMatrix();

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(cx, cy, cz);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    const robotGroup = new THREE.Group();
    const cone = new THREE.Mesh(
        new THREE.ConeGeometry(0.18, 0.5, 20),
        new THREE.MeshBasicMaterial({ color: 0xef4444 })
    );
    cone.rotation.x = Math.PI / 2;
    robotGroup.add(cone);
    scene.add(robotGroup);
    robotGroup.position.set(cx, cy, zPlane);

    const trailMax = 8000;
    const trailPos = new Float32Array(trailMax * 3);
    const trailGeom = new THREE.BufferGeometry();
    const trailAttr = new THREE.BufferAttribute(trailPos, 3);
    trailGeom.setAttribute('position', trailAttr);
    trailGeom.setDrawRange(0, 0);
    const trailLine = new THREE.Line(trailGeom, new THREE.LineBasicMaterial({ color: 0xf97316 }));
    scene.add(trailLine);
    let trailCount = 0;

    const wpGroup = new THREE.Group();
    scene.add(wpGroup);
    const avoidGroup = new THREE.Group();
    scene.add(avoidGroup);

    let raf = 0;
    function tick() {
        raf = requestAnimationFrame(tick);
        controls.update();
        renderer.render(scene, camera);
    }
    tick();

    function resize() {
        const w = container.clientWidth || 2;
        const h = container.clientHeight || 2;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h, false);
    }

    function setRobotXYYaw(x, y, yawRad) {
        robotGroup.position.set(x, y, zPlane);
        if (yawRad != null && Number.isFinite(yawRad)) {
            robotGroup.rotation.set(0, 0, yawRad);
        }
    }

    function pushTrail(x, y) {
        if (trailCount >= trailMax) {
            trailCount = 0;
        }
        const o = trailCount * 3;
        trailPos[o] = x;
        trailPos[o + 1] = y;
        trailPos[o + 2] = zPlane;
        trailCount += 1;
        trailAttr.needsUpdate = true;
        trailGeom.setDrawRange(0, trailCount);
    }

    function clearTrail() {
        trailCount = 0;
        trailGeom.setDrawRange(0, 0);
    }

    function clearGroup(g) {
        while (g.children.length) {
            const ch = g.children[0];
            g.remove(ch);
            if (ch.geometry) ch.geometry.dispose();
            if (ch.material) ch.material.dispose();
        }
    }

    function syncWaypoints(waypointsRaw, pickupsRaw) {
        clearGroup(wpGroup);
        const dotGeo = new THREE.SphereGeometry(0.12, 10, 10);
        const wpMat = new THREE.MeshBasicMaterial({ color: 0x7c3aed });
        const pkMat = new THREE.MeshBasicMaterial({ color: 0xfb923c });
        (waypointsRaw || []).forEach(function (w) {
            const c = w && w.center;
            if (!c || c.x == null || c.y == null) return;
            const m = new THREE.Mesh(dotGeo, wpMat);
            m.position.set(Number(c.x), Number(c.y), zPlane + 0.05);
            wpGroup.add(m);
        });
        (pickupsRaw || []).forEach(function (p) {
            if (!p || p.x == null || p.y == null) return;
            const m = new THREE.Mesh(dotGeo, pkMat);
            m.position.set(Number(p.x), Number(p.y), zPlane + 0.05);
            wpGroup.add(m);
        });
    }

    function syncAvoidance(xs, ys) {
        clearGroup(avoidGroup);
        if (!Array.isArray(xs) || !Array.isArray(ys) || xs.length < 1) return;
        const n = Math.min(xs.length, ys.length);
        const arr = new Float32Array(n * 3);
        for (let i = 0; i < n; i++) {
            arr[i * 3] = Number(xs[i]);
            arr[i * 3 + 1] = Number(ys[i]);
            arr[i * 3 + 2] = zPlane + 0.08;
        }
        if (n > 1) {
            const g = new THREE.BufferGeometry();
            g.setAttribute('position', new THREE.BufferAttribute(arr, 3));
            const line = new THREE.Line(
                g,
                new THREE.LineDashedMaterial({ color: 0xd97706, dashSize: 0.25, gapSize: 0.18 })
            );
            line.computeLineDistances();
            avoidGroup.add(line);
        }
        for (let i = 0; i < n; i++) {
            const sg = new THREE.SphereGeometry(0.1, 8, 8);
            const sm = new THREE.Mesh(sg, new THREE.MeshBasicMaterial({ color: 0xfbbf24 }));
            sm.position.set(arr[i * 3], arr[i * 3 + 1], arr[i * 3 + 2]);
            avoidGroup.add(sm);
        }
    }

    function dispose() {
        cancelAnimationFrame(raf);
        controls.dispose();
        geom.dispose();
        mat.dispose();
        trailGeom.dispose();
        trailLine.material.dispose();
        clearGroup(wpGroup);
        clearGroup(avoidGroup);
        renderer.dispose();
        if (renderer.domElement.parentNode) renderer.domElement.parentNode.removeChild(renderer.domElement);
    }

    resize();
    return {
        resize,
        dispose,
        setRobotXYYaw,
        pushTrail,
        clearTrail,
        syncWaypoints,
        syncAvoidance,
        zPlane,
    };
}
