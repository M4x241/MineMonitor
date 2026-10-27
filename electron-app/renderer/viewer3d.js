console.log('[viewer3d] module loaded');

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { PLYLoader } from 'three/addons/loaders/PLYLoader.js';
import { OBJLoader } from 'three/addons/loaders/OBJLoader.js';
import { MTLLoader } from 'three/addons/loaders/MTLLoader.js';

const _NEIGHBORS = [
  [1, 0, 0], [-1, 0, 0],
  [0, 1, 0], [0, -1, 0],
  [0, 0, 1], [0, 0, -1],
];

export class Viewer3D {
  constructor(container, fetch) {
    this.container = container;
    this.fetch = fetch;
    this.onBack = null;
    this.voxelData = [];
    this.voxelGroup = new THREE.Group();
    this.voxelSize = 1.0;
    this._raf = 0;
    this._refreshTimer = null;
    this._highlightTimer = null;
    this._missionId = null;
    this._highlightGroup = new THREE.Group();
    this.noRoof = false;
    this.showPath = true;
    this.levelFilter = null;
    this.externalMode = false;

    this._buildScene();
    this._buildOverlay();
    this._bindResize();
    this._animate();
  }

  _buildScene() {
    const w = this.container.clientWidth || 1280;
    const h = this.container.clientHeight || 768;

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0a0a0f);
    this.scene.fog = new THREE.FogExp2(0x0a0a0f, 0.002);

    this.camera = new THREE.PerspectiveCamera(60, w / h, 0.1, 600);
    this.camera.position.set(50, 40, 50);

    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setSize(w, h);
    this.renderer.setPixelRatio(window.devicePixelRatio);
    this.container.appendChild(this.renderer.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.target.set(32, 16, 32);

    this.scene.add(new THREE.AmbientLight(0x505060, 1.0));
    const dir = new THREE.DirectionalLight(0xffffff, 1.2);
    dir.position.set(60, 100, 60);
    this.scene.add(dir);
    const point = new THREE.PointLight(0x4488ff, 0.6, 150);
    point.position.set(32, 25, 32);
    this.scene.add(point);

    this.grid = new THREE.GridHelper(128, 64, 0x222233, 0x111122);
    this.grid.position.y = -0.5;
    this.scene.add(this.grid);

    this.scene.add(this.voxelGroup);
    this.scene.add(this._highlightGroup);
    this.boxGeo = new THREE.BoxGeometry(1, 1, 1);
    this.dummy = new THREE.Object3D();
  }

  _buildOverlay() {
    this.overlay = document.createElement('div');
    this.overlay.className = 'v3d-overlay';
    this.overlay.innerHTML = `
      <div class="v3d-topbar">
        <span class="v3d-title" id="v3dTitle">Mapa</span>
        <button class="v3d-roof-toggle" id="v3dRoof" title="Alternar entre mina completa y sin techo para ver el camino interno">Ver sin techo</button>
        <button class="v3d-roof-toggle" id="v3dPath" title="Mostrar/ocultar el recorrido del dron">Recorrido: on</button>
        <button class="v3d-back" id="v3dBack">&larr; Volver</button>
      </div>
      <div class="v3d-controls">
        <label id="v3dSizeWrap">Voxel size
          <input id="v3dSize" type="range" min="0.5" max="1.5" step="0.05" value="1.0">
        </label>
        <label id="v3dLevelWrap">Nivel
          <select id="v3dLevel">
            <option value="">Todos</option>
            <option value="0">1</option>
            <option value="1">2</option>
            <option value="2">3</option>
            <option value="3">4</option>
          </select>
        </label>
        <label><input id="v3dGrid" type="checkbox" checked> Mostrar grid</label>
      </div>
      <div class="v3d-stats" id="v3dStats"></div>
      <div class="v3d-loading" id="v3dLoading" style="display:none">
        <div class="v3d-spinner"></div>
        <h3 id="v3dLoadTitle">Cargando...</h3>
        <p id="v3dLoadDetail"></p>
      </div>`;

    this.container.appendChild(this.overlay);

    this.doc$ = (id) => this.overlay.querySelector('#' + id);
    this.doc$('v3dBack').addEventListener('click', () => this.onBack && this.onBack());
    this.doc$('v3dSize').addEventListener('input', (e) => {
      this.voxelSize = parseFloat(e.target.value);
      this.voxelGroup.children.forEach((m) => {
        if (m.isInstancedMesh) m.scale.set(this.voxelSize, this.voxelSize, this.voxelSize);
      });
    });
    this.doc$('v3dGrid').addEventListener('change', (e) => {
      this.grid.visible = e.target.checked;
    });
    this.doc$('v3dRoof').addEventListener('click', () => {
      this.noRoof = !this.noRoof;
      this.doc$('v3dRoof').textContent = this.noRoof ? 'Ver con techo' : 'Ver sin techo';
      this.buildMesh(false);
    });
    this.doc$('v3dPath').addEventListener('click', () => {
      this.showPath = !this.showPath;
      this.doc$('v3dPath').textContent = this.showPath ? 'Recorrido: on' : 'Recorrido: off';
      this.buildMesh(false);
    });
    this.doc$('v3dLevel').addEventListener('change', (e) => {
      const v = e.target.value;
      this.levelFilter = v === '' ? null : parseInt(v, 10);
      this.buildMesh(false);
    });
  }

  _bindResize() {
    this._ro = new ResizeObserver(() => {
      const w = this.container.clientWidth;
      const h = this.container.clientHeight;
      if (!w || !h) return;
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
      this.renderer.setSize(w, h);
    });
    this._ro.observe(this.container);
  }

  _animate() {
    const loop = () => {
      this._raf = requestAnimationFrame(loop);
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
    };
    loop();
  }

  async _fetchAllVoxels(missionId) {
    const size = 20000;
    let page = 0, voxels = [];
    for (;;) {
      const r = await this.fetch('/missions/' + missionId + '/map?page=' + page + '&size=' + size);
      if (r.status !== 200) throw new Error(r.data?.detail || 'HTTP ' + r.status);
      const data = r.data || {};
      voxels = voxels.concat(data.voxels || []);
      if (!data.has_more) return { voxels, bounds: data.bounds, total: data.total_voxels };
      page++;
      if (page > 200) throw new Error('Demasiadas páginas al cargar los voxeles');
    }
  }

  async loadMap(missionId, missionName) {
    this.externalMode = false;
    this._missionId = missionId;
    this._setMissionControls(true);
    this.grid.position.set(0, -0.5, 0);
    this.doc$('v3dTitle').textContent = 'Misión ' + missionId + (missionName ? ' - ' + missionName : '');
    const loading = this.doc$('v3dLoading');
    const title = this.doc$('v3dLoadTitle');
    const detail = this.doc$('v3dLoadDetail');
    loading.style.display = 'block';
    title.textContent = 'Cargando misión ' + missionId + '...';

    try {
      const data = await this._fetchAllVoxels(missionId);
      this.voxelData = data.voxels || [];
      this.v3dBounds = data.bounds;
      detail.textContent = data.voxels.length + ' voxels recibidos';
      this.buildMesh();
    } catch (e) {
      loading.style.display = 'block';
      title.textContent = 'Error';
      detail.textContent = e.message || String(e);
      return;
    }

    if (this._refreshTimer) clearInterval(this._refreshTimer);
    this._refreshTimer = setInterval(() => this._refreshMap(), 3000);
  }

  // ── Modelos externos (PLY / OBJ) ─────────────────────
  async loadExternal(files) {
    if (this._refreshTimer) { clearInterval(this._refreshTimer); this._refreshTimer = null; }
    this._missionId = null;
    this.externalMode = true;
    this._highlightGroup.clear();

    const loading = this.doc$('v3dLoading');
    const names = (files || []).map((f) => f.name).join(', ');
    loading.style.display = 'block';
    this.doc$('v3dTitle').textContent = 'Importado - ' + names;
    this.doc$('v3dLoadTitle').textContent = 'Cargando modelo...';
    this.doc$('v3dLoadDetail').textContent = names;
    this._setMissionControls(false);

    try {
      const objects = this._parseExternal(files || []);
      if (!objects.length) throw new Error('No se encontró ningún archivo PLY u OBJ en la selección');
      this._buildExternal(objects);
    } catch (e) {
      loading.style.display = 'block';
      this.doc$('v3dLoadTitle').textContent = 'Error al importar';
      this.doc$('v3dLoadDetail').textContent = e.message || String(e);
    }
  }

  _parseExternal(files) {
    const out = [];
    const plyFile = files.find((f) => f.ext === 'ply');
    const objFile = files.find((f) => f.ext === 'obj');
    const mtlFile = files.find((f) => f.ext === 'mtl');

    if (plyFile) {
      const d = plyFile.data;
      const ab = d.buffer.slice(d.byteOffset, d.byteOffset + d.byteLength);
      const geo = new PLYLoader().parse(ab, '');
      if (!geo.getAttribute('normal')) geo.computeVertexNormals();
      const hasFaces = !!geo.index && geo.index.count > 0;
      out.push({ name: plyFile.name, geometry: geo, kind: hasFaces ? 'mesh' : 'points' });
    }

    if (objFile) {
      let materials = null;
      if (mtlFile) {
        try {
          const mtlText = new TextDecoder().decode(mtlFile.data);
          materials = new MTLLoader().parse(mtlText, '');
          materials.preload();
        } catch (_e) {
          materials = null;
        }
      }
      const objText = new TextDecoder().decode(objFile.data);
      const loader = new OBJLoader();
      if (materials) loader.setMaterials(materials);
      const group = loader.parse(objText);
      group.traverse((child) => {
        if (!child.isMesh || !child.geometry) return;
        if (!child.geometry.getAttribute('normal')) child.geometry.computeVertexNormals();
        const hasColor = !!child.geometry.getAttribute('color');
        if (!materials) {
          child.material = new THREE.MeshLambertMaterial({ color: 0xbfc4cc, vertexColors: hasColor });
        } else if (hasColor) {
          child.material.vertexColors = true;
          child.material.needsUpdate = true;
        }
      });
      out.push({ name: objFile.name, object: group, kind: 'mesh' });
    }
    return out;
  }

  _buildExternal(objects) {
    this.scene.remove(this.voxelGroup);
    this.voxelGroup = new THREE.Group();
    this.scene.add(this.voxelGroup);

    const box = new THREE.Box3();
    let verts = 0;
    let tris = 0;
    for (const o of objects) {
      if (o.kind === 'points') {
        const geo = o.geometry;
        const hasColor = !!geo.getAttribute('color');
        const mat = new THREE.PointsMaterial({
          size: 0.8, vertexColors: hasColor, color: hasColor ? 0xffffff : 0x66ccff,
        });
        this.voxelGroup.add(new THREE.Points(geo, mat));
        geo.computeBoundingBox();
        if (geo.boundingBox) box.union(geo.boundingBox);
        verts += geo.getAttribute('position').count;
      } else {
        let target = o.object;
        if (!target) {
          const geo = o.geometry;
          const hasColor = !!geo.getAttribute('color');
          target = new THREE.Mesh(geo, new THREE.MeshLambertMaterial({
            color: 0xbfc4cc, vertexColors: hasColor, side: THREE.DoubleSide,
          }));
        }
        this.voxelGroup.add(target);
        target.traverse((c) => {
          if (!c.isMesh || !c.geometry) return;
          const pos = c.geometry.getAttribute('position');
          if (!pos) return;
          verts += pos.count;
          tris += c.geometry.index ? c.geometry.index.count / 3 : pos.count / 3;
        });
        box.expandByObject(target);
      }
    }

    if (box.isEmpty()) throw new Error('El modelo no tiene geometría visible');

    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const radius = Math.max(size.x, size.y, size.z, 1);

    this.voxelGroup.traverse((c) => {
      if (c.isPoints && c.material) c.material.size = Math.max(radius / 250, 0.05);
    });

    this.grid.position.set(center.x, box.min.y - 0.5, center.z);
    this.controls.target.copy(center);
    this.camera.position.set(center.x + radius, center.y + radius * 0.8, center.z + radius);
    this.camera.near = Math.max(radius / 5000, 0.01);
    this.camera.far = radius * 50;
    this.camera.updateProjectionMatrix();
    this.controls.update();

    this.doc$('v3dStats').innerHTML =
      '<span>' + verts.toLocaleString() + '</span> vértices &middot; ' +
      '<span>' + Math.round(tris).toLocaleString() + '</span> triángulos &middot; modelo importado';
    this.doc$('v3dLoading').style.display = 'none';
  }

  _setMissionControls(enabled) {
    for (const id of ['v3dRoof', 'v3dPath', 'v3dSizeWrap', 'v3dLevelWrap']) {
      const el = this.doc$(id);
      if (el) el.style.display = enabled ? '' : 'none';
    }
  }

  async _refreshMap() {
    if (!this._missionId) return;
    try {
      const data = await this._fetchAllVoxels(this._missionId);
      const fresh = data.voxels || [];
      if (fresh.length === this.voxelData.length) return;
      const prev = new Set(this.voxelData.map((v) => v.pos.x + ',' + v.pos.y + ',' + v.pos.z));
      const added = fresh.filter((v) => !prev.has(v.pos.x + ',' + v.pos.y + ',' + v.pos.z));
      this.voxelData = fresh;
      this.v3dBounds = data.bounds;
      this.buildMesh();
      if (added.length) {
        this._flashAdded(added);
        const detail = this.doc$('v3dLoadDetail');
        detail.textContent = this.voxelData.length + ' voxels (nuevos: ' + added.length + ')';
      }
    } catch (_e) {
      /* el siguiente ciclo reintentará */
    }
  }

  _flashAdded(added) {
    if (this._highlightGroup.parent !== this.scene) this.scene.add(this._highlightGroup);
    const mat = new THREE.MeshBasicMaterial({ color: 0xff8844, transparent: true, opacity: 0.9 });
    const im = new THREE.InstancedMesh(this.boxGeo, mat, added.length);
    for (let i = 0; i < added.length; i++) {
      this.dummy.position.set(added[i].pos.x, added[i].pos.y, added[i].pos.z);
      this.dummy.updateMatrix();
      im.setMatrixAt(i, this.dummy.matrix);
    }
    im.instanceMatrix.needsUpdate = true;
    im.scale.set(1.05, 1.05, 1.05);
    this._highlightGroup.clear();
    this._highlightGroup.add(im);
    const prev = this._highlightTimer;
    if (prev) clearTimeout(prev);
    this._highlightTimer = setTimeout(() => this._highlightGroup.clear(), 1200);
  }

  buildMesh(reframe = true) {
    this.scene.remove(this.voxelGroup);
    this.voxelGroup = new THREE.Group();
    this.scene.add(this.voxelGroup);

    const loading = this.doc$('v3dLoading');
    const all = this._voxelsVisible();
    if (!all.length) {
      loading.style.display = 'block';
      this.doc$('v3dLoadTitle').textContent = 'Sin voxeles';
      this.doc$('v3dLoadDetail').textContent = 'No hay datos para esta misión' +
        (this.levelFilter != null ? ' en el nivel ' + (this.levelFilter + 1) : '') + '.';
      return;
    }

    // Solo se dibujan las PAREDES (sólidos con al menos un vecino de aire),
    // así los pasillos quedan huecos y el mapa se ve como Minecraft.
    const key = (p) => p.x + ',' + p.y + ',' + p.z;
    const air = new Map();
    for (const v of all) if (v.state === 1 && !air.has(key(v.pos))) air.set(key(v.pos), true);
    let walls = [];
    for (const v of all) {
      if (v.state !== 2) continue;
      if (this._hasAirNeighbor(v.pos, air)) walls.push(v);
    }

    // "Sin techo": quita los bloques que tienen aire justo debajo (techo de los
    // pasillos), dejando suelo y muros, para poder ver el camino interno desde arriba.
    let modo = 'mina completa';
    if (this.noRoof) {
      walls = walls.filter((v) => !this._hasAirBelow(v.pos, air));
      modo = 'sin techo';
    }

    const boxGeo = this.boxGeo;
    const wallMat = new THREE.MeshLambertMaterial({ color: 0xffffff });
    const im = new THREE.InstancedMesh(boxGeo, wallMat, walls.length);

    for (let i = 0; i < walls.length; i++) {
      this.dummy.position.set(walls[i].pos.x, walls[i].pos.y, walls[i].pos.z);
      this.dummy.updateMatrix();
      im.setMatrixAt(i, this.dummy.matrix);
      im.setColorAt(i, this._wallColor(walls[i]));
    }
    im.instanceMatrix.needsUpdate = true;
    if (im.instanceColor) im.instanceColor.needsUpdate = true;
    im.scale.set(this.voxelSize, this.voxelSize, this.voxelSize);
    this.voxelGroup.add(im);

    // Línea del recorrido explorado (voxeles de aire visitados, ordenados por paso).
    this.voxelGroup.add(this._buildPath(all));

    if (reframe) {
      const total = all.length;
      let minX = Infinity, minY = Infinity, minZ = Infinity;
      let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
      for (const v of all) {
        if (v.pos.x < minX) minX = v.pos.x; if (v.pos.y < minY) minY = v.pos.y; if (v.pos.z < minZ) minZ = v.pos.z;
        if (v.pos.x > maxX) maxX = v.pos.x; if (v.pos.y > maxY) maxY = v.pos.y; if (v.pos.z > maxZ) maxZ = v.pos.z;
      }
      this.controls.target.set((minX + maxX) / 2, (minY + maxY) / 2, (minZ + maxZ) / 2);
      this.camera.position.set((minX + maxX) / 2 + 32, (minY + maxY) / 2 + 28, (minZ + maxZ) / 2 + 32);
    }

    loading.style.display = 'none';
    let risks = 0, entropySum = 0;
    for (const v of all) {
      if ((v.risk | 0) > 0) risks++;
      entropySum += v.entropy || 0;
    }
    const entropyAvg = all.length ? (entropySum / all.length) : 0;
    let pathLen = 0;
    for (const v of all) if (v.state === 1 && typeof v.step === 'number') pathLen++;
    const nivel = this.levelFilter != null ? ('nivel ' + (this.levelFilter + 1)) : 'todos los niveles';
    this.doc$('v3dStats').innerHTML =
      '<span>' + walls.length + '</span> de pared &middot; <span>' + all.length + '</span> voxels totales &middot; ' +
      'recorrido <span>' + pathLen + '</span> bloques &middot; riesgo <span>' + risks + '</span> &middot; ' +
      'entropía media <span>' + entropyAvg.toFixed(2) + '</span> &middot; ' + modo + ' &middot; ' + nivel;
  }

  _levelIndex(v) {
    const b = Math.floor(v.pos.x / 112);
    return b < 0 ? 0 : b > 3 ? 3 : b;
  }

  _voxelsVisible() {
    if (this.levelFilter == null) return this.voxelData;
    return this.voxelData.filter((v) => this._levelIndex(v) === this.levelFilter);
  }

  _buildPath(all) {
    const group = new THREE.Group();
    if (!this.showPath) return group;
    const visited = all
      .filter((v) => v.state === 1 && typeof v.step === 'number')
      .sort((a, b) => a.step - b.step);
    if (visited.length < 2) return group;

    const material = new THREE.LineBasicMaterial({ color: 0x00ddff });
    const startDot = new THREE.Mesh(
      new THREE.SphereGeometry(0.35, 8, 8),
      new THREE.MeshBasicMaterial({ color: 0x22ff88 })
    );
    const endDot = new THREE.Mesh(
      new THREE.SphereGeometry(0.35, 8, 8),
      new THREE.MeshBasicMaterial({ color: 0xff4422 })
    );

    const pts = [];
    let prev = null;
    let first = null;
    const flush = () => {
      if (pts.length >= 2) {
        const geo = new THREE.BufferGeometry().setFromPoints(pts);
        group.add(new THREE.Line(geo, material));
      }
      pts.length = 0;
    };
    for (const v of visited) {
      const p = new THREE.Vector3(v.pos.x + 0.5, v.pos.y + 0.5, v.pos.z + 0.5);
      // Salto grande = cambio de nivel/teletransporte: corta la línea ahí.
      if (prev && p.distanceTo(prev) > 40) flush();
      if (!first) first = p.clone();
      pts.push(p);
      prev = p;
    }
    flush();
    if (first) startDot.position.copy(first);
    if (prev) endDot.position.copy(prev);
    group.add(startDot);
    group.add(endDot);
    return group;
  }

  _hasAirNeighbor(pos, air) {
    for (const d of _NEIGHBORS) {
      if (air.has((pos.x + d[0]) + ',' + (pos.y + d[1]) + ',' + (pos.z + d[2]))) return true;
    }
    return false;
  }

  _hasAirBelow(pos, air) {
    return air.has(pos.x + ',' + (pos.y - 1) + ',' + pos.z);
  }

  _wallColor(v) {
    const r = v.risk | 0;
    if (r >= 4) return new THREE.Color(0xff6a3d);      // lava / magma
    if (r === 3) return new THREE.Color(0x7a7a94);     // escombros / andesita
    if (r === 2) return new THREE.Color(0xa08a5e);     // grava / tierra
    if (r === 1) return new THREE.Color(0x9c4f3d);     // grietas
    return new THREE.Color(0x8e8e96);                   // piedra
  }

  dispose() {
    cancelAnimationFrame(this._raf);
    if (this._refreshTimer) clearInterval(this._refreshTimer);
    if (this._highlightTimer) clearTimeout(this._highlightTimer);
    if (this._ro) this._ro.disconnect();
    if (this.renderer) {
      this.renderer.dispose();
      if (this.renderer.domElement && this.renderer.domElement.parentNode) {
        this.renderer.domElement.parentNode.removeChild(this.renderer.domElement);
      }
    }
    if (this.overlay && this.overlay.parentNode) {
      this.overlay.parentNode.removeChild(this.overlay);
    }
  }
}