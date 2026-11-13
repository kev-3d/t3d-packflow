import { useEffect, useImperativeHandle, useRef, useState, type Ref } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { CSS2DObject, CSS2DRenderer } from 'three/addons/renderers/CSS2DRenderer.js'
import type { PackedBox, Placement } from '../../types'
import {
  colorForProduct, containerSceneBox, fitDistance, isInHeightLayer, placementSceneBox,
  ZOOM_MAX, ZOOM_MIN, type BoxSceneHandle, type HeightLayer,
} from './sceneBuilder'

export interface BoxSceneProps {
  box: PackedBox
  top: boolean
  labels: boolean
  layer: HeightLayer
  selected: string
  product: string
  onSelect: (uid: string) => void
  onZoomChange: (zoom: number) => void
  ref?: Ref<BoxSceneHandle>
}

interface UnitView {
  placement: Placement
  mesh: THREE.Mesh
  material: THREE.MeshLambertMaterial
  edgeMaterial: THREE.LineBasicMaterial
  label: CSS2DObject
}

interface SceneState {
  camera: THREE.PerspectiveCamera
  controls: OrbitControls
  units: Map<string, UnitView>
  baseDistance: number
  requestRender: () => void
}

const FOV = 40
const HOME_POLAR = THREE.MathUtils.degToRad(58)
const HOME_AZIMUTH = THREE.MathUtils.degToRad(32)
const TOP_POLAR = 0.0001
const FLOOR_LIMIT = Math.PI / 2 - 0.02
const UP = new THREE.Vector3(0, 1, 0)

function placeCamera(state: SceneState, distance: number, polar: number, azimuth: number) {
  const { camera, controls } = state
  camera.position.copy(controls.target).add(new THREE.Vector3().setFromSphericalCoords(distance, polar, azimuth))
  controls.update()
}

function applyViewMode(state: SceneState, top: boolean) {
  const { controls, camera } = state
  controls.minPolarAngle = top ? TOP_POLAR : 0
  controls.maxPolarAngle = top ? TOP_POLAR : FLOOR_LIMIT
  const offset = new THREE.Spherical().setFromVector3(camera.position.clone().sub(controls.target))
  // Top view is aligned with the box axes (length left-right), like a floor plan.
  placeCamera(state, offset.radius, top ? TOP_POLAR : HOME_POLAR, top ? 0 : offset.theta)
}

function applyUnitState(state: SceneState, { labels, layer, selected, product }: Pick<BoxSceneProps, 'labels' | 'layer' | 'selected' | 'product'>) {
  for (const unit of state.units.values()) {
    const { placement, mesh, material, edgeMaterial, label } = unit
    const visible = isInHeightLayer(placement, layer)
    const dimmed = Boolean(product) && product !== placement.sku
    const isSelected = selected === placement.uid
    mesh.visible = visible
    label.visible = visible && labels
    material.transparent = dimmed
    material.opacity = dimmed ? 0.18 : 1
    material.depthWrite = !dimmed
    material.emissive.set(isSelected ? '#389D9B' : '#000000')
    material.emissiveIntensity = isSelected ? 0.45 : 0
    edgeMaterial.color.set(isSelected ? '#0B3B3A' : '#5F6B73')
    edgeMaterial.transparent = dimmed
    edgeMaterial.opacity = dimmed ? 0.25 : 1
    label.element.style.opacity = dimmed ? '0.35' : '1'
    label.element.classList.toggle('box3d-label--selected', isSelected)
  }
  state.requestRender()
}

function textLabel(className: string, text: string, detail?: string) {
  const element = document.createElement('div')
  element.className = className
  element.textContent = text
  if (detail) {
    const small = document.createElement('small')
    small.textContent = detail
    element.append(small)
  }
  return new CSS2DObject(element)
}

function webglAvailable() {
  try {
    const canvas = document.createElement('canvas')
    return Boolean(canvas.getContext('webgl2') || canvas.getContext('webgl'))
  } catch {
    return false
  }
}

export default function BoxScene({ box, top, labels, layer, selected, product, onSelect, onZoomChange, ref }: BoxSceneProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const stateRef = useRef<SceneState | null>(null)
  const [failed, setFailed] = useState(false)
  const latest = useRef({ top, labels, layer, selected, product, onSelect, onZoomChange })
  latest.current = { top, labels, layer, selected, product, onSelect, onZoomChange }

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    if (!webglAvailable()) { setFailed(true); return }
    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    } catch {
      setFailed(true)
      return
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    renderer.domElement.className = 'box3d-canvas'
    host.append(renderer.domElement)
    const labelRenderer = new CSS2DRenderer()
    labelRenderer.domElement.className = 'box3d-labels'
    host.append(labelRenderer.domElement)

    const scene = new THREE.Scene()
    scene.add(new THREE.HemisphereLight('#ffffff', '#b8c4cc', 2.2))
    const sun = new THREE.DirectionalLight('#ffffff', 1.4)
    sun.position.set(2, 4, 3)
    scene.add(sun)

    // Container: floor, outline and dimension labels.
    const container = containerSceneBox(box)
    const [sx, sy, sz] = container.size
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(sx, sz), new THREE.MeshBasicMaterial({ color: '#DCE3E7', side: THREE.DoubleSide }))
    floor.rotation.x = -Math.PI / 2
    floor.position.y = -0.002
    scene.add(floor)
    const outline = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(sx, sy, sz)), new THREE.LineBasicMaterial({ color: '#7D95A3' }))
    outline.position.set(...container.center)
    scene.add(outline)
    const [length, width, height] = box.dimensions_cm
    const gap = 0.06
    const lengthLabel = textLabel('box3d-measure', `${length} cm`)
    lengthLabel.position.set(0, 0, sz / 2 + gap)
    const widthLabel = textLabel('box3d-measure', `${width} cm`)
    widthLabel.position.set(sx / 2 + gap, 0, 0)
    const heightLabel = textLabel('box3d-measure', `${height} cm`)
    heightLabel.position.set(-sx / 2 - gap, sy / 2, sz / 2)
    scene.add(lengthLabel, widthLabel, heightLabel)

    // One mesh per placed unit, with its edges and a name label as children.
    const units = new Map<string, UnitView>()
    for (const placement of box.placements) {
      const { center, size } = placementSceneBox(placement, box)
      const geometry = new THREE.BoxGeometry(...size)
      const material = new THREE.MeshLambertMaterial({ color: colorForProduct(placement.sku) })
      const mesh = new THREE.Mesh(geometry, material)
      mesh.position.set(...center)
      mesh.userData.uid = placement.uid
      const edgeMaterial = new THREE.LineBasicMaterial({ color: '#5F6B73' })
      mesh.add(new THREE.LineSegments(new THREE.EdgesGeometry(geometry), edgeMaterial))
      const label = textLabel('box3d-label', placement.name, `Z${placement.position[2]}`)
      label.position.set(0, size[1] / 2, 0)
      mesh.add(label)
      scene.add(mesh)
      units.set(placement.uid, { placement, mesh, material, edgeMaterial, label })
    }

    const camera = new THREE.PerspectiveCamera(FOV, 1, 0.01, 100)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enablePan = false
    controls.target.set(0, sy / 2, 0)
    const baseDistance = fitDistance(box, FOV)
    controls.minDistance = baseDistance / ZOOM_MAX
    controls.maxDistance = baseDistance / ZOOM_MIN

    let frame = 0
    const requestRender = () => {
      if (frame) return
      frame = requestAnimationFrame(() => {
        frame = 0
        renderer.render(scene, camera)
        labelRenderer.render(scene, camera)
      })
    }
    const state: SceneState = { camera, controls, units, baseDistance, requestRender }
    stateRef.current = state
    let lastZoom = 1
    controls.addEventListener('change', () => {
      requestRender()
      const zoom = Math.round((baseDistance / camera.position.distanceTo(controls.target)) * 100) / 100
      if (zoom !== lastZoom) { lastZoom = zoom; latest.current.onZoomChange(zoom) }
    })

    placeCamera(state, baseDistance, HOME_POLAR, HOME_AZIMUTH)
    applyViewMode(state, latest.current.top)
    applyUnitState(state, latest.current)

    const resize = () => {
      const width = host.clientWidth, height = host.clientHeight
      if (!width || !height) return
      renderer.setSize(width, height)
      labelRenderer.setSize(width, height)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      requestRender()
    }
    const observer = new ResizeObserver(resize)
    observer.observe(host)
    resize()

    // Click (not drag) picks a unit; hovering a unit shows a pointer cursor.
    const raycaster = new THREE.Raycaster()
    const pointer = new THREE.Vector2()
    const pick = (event: PointerEvent) => {
      const rect = renderer.domElement.getBoundingClientRect()
      pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1)
      raycaster.setFromCamera(pointer, camera)
      const meshes = [...units.values()].filter(u => u.mesh.visible && u.material.opacity === 1).map(u => u.mesh)
      return raycaster.intersectObjects(meshes, false)[0]?.object.userData.uid as string | undefined
    }
    let down: { x: number; y: number } | null = null
    const onDown = (event: PointerEvent) => { down = { x: event.clientX, y: event.clientY } }
    const onUp = (event: PointerEvent) => {
      if (down && Math.hypot(event.clientX - down.x, event.clientY - down.y) < 5) {
        const uid = pick(event)
        if (uid) latest.current.onSelect(uid)
      }
      down = null
    }
    const onMove = (event: PointerEvent) => {
      if (event.buttons) return
      renderer.domElement.style.cursor = pick(event) ? 'pointer' : 'grab'
    }
    renderer.domElement.addEventListener('pointerdown', onDown)
    renderer.domElement.addEventListener('pointerup', onUp)
    renderer.domElement.addEventListener('pointermove', onMove)

    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      renderer.domElement.removeEventListener('pointerdown', onDown)
      renderer.domElement.removeEventListener('pointerup', onUp)
      renderer.domElement.removeEventListener('pointermove', onMove)
      controls.dispose()
      scene.traverse(object => {
        if (object instanceof THREE.Mesh || object instanceof THREE.LineSegments) {
          object.geometry.dispose()
          const materials = Array.isArray(object.material) ? object.material : [object.material]
          materials.forEach(material => material.dispose())
        }
      })
      renderer.dispose()
      renderer.domElement.remove()
      labelRenderer.domElement.remove()
      stateRef.current = null
    }
  }, [box])

  useEffect(() => { if (stateRef.current) applyViewMode(stateRef.current, top) }, [top])
  useEffect(() => { if (stateRef.current) applyUnitState(stateRef.current, { labels, layer, selected, product }) }, [labels, layer, selected, product])

  useImperativeHandle(ref, () => ({
    rotate(degrees) {
      const state = stateRef.current
      if (!state) return
      const offset = state.camera.position.clone().sub(state.controls.target).applyAxisAngle(UP, THREE.MathUtils.degToRad(-degrees))
      state.camera.position.copy(state.controls.target).add(offset)
      state.controls.update()
    },
    zoomBy(delta) {
      const state = stateRef.current
      if (!state) return
      const offset = new THREE.Spherical().setFromVector3(state.camera.position.clone().sub(state.controls.target))
      const zoom = THREE.MathUtils.clamp(state.baseDistance / offset.radius + delta, ZOOM_MIN, ZOOM_MAX)
      placeCamera(state, state.baseDistance / zoom, offset.phi, offset.theta)
    },
    reset() {
      const state = stateRef.current
      if (!state) return
      placeCamera(state, state.baseDistance, latest.current.top ? TOP_POLAR : HOME_POLAR, latest.current.top ? 0 : HOME_AZIMUTH)
    },
  }), [])

  if (failed) {
    return <div className="box3d-fallback" role="status">The 3D view is unavailable because this browser could not start WebGL. Use the unit selector and the product legend to inspect the plan.</div>
  }
  const [length, width, height] = box.dimensions_cm
  return <div ref={hostRef} className="box3d-host" role="img" aria-label={`3D view of box ${box.sequence}: ${box.placements.length} ${box.placements.length === 1 ? 'unit' : 'units'} in a ${length} × ${width} × ${height} cm box. Drag to rotate and scroll to zoom.`} />
}
