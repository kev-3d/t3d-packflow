import type { PackedBox, Placement } from '../../types'

/**
 * Pure geometry helpers for the 3D box viewer. No DOM, WebGL or three.js imports,
 * so this module can be exercised directly by `node --test`.
 *
 * Engine coordinates (cm): x runs along the box length, y along its width, z is height.
 * Scene coordinates (three.js, Y-up): X = x, Y = z, Z = -y. Negating y keeps the
 * right-handed orientation, so the scene is not a mirror image of the plan.
 * The box floor is centred on the origin and everything is scaled by 1 / max(L, W, H).
 */

const palette = ['#E5F3F2', '#FFF0E4', '#E5F3E2', '#FCEAEA', '#EDEDEB', '#F5F5F3']

/** Stable product colour; identical to the original 2D viewer so the legend keeps its colours. */
export function colorForProduct(key: string) {
  let hash = 0
  for (const char of key) hash = (hash * 31 + char.charCodeAt(0)) >>> 0
  return palette[hash % palette.length]
}

export type Vec3 = [number, number, number]
export type HeightLayer = 'all' | number

export interface SceneBox {
  /** Centre of the box mesh in scene units. */
  center: Vec3
  /** Size along scene X, Y, Z. */
  size: Vec3
}

export function sceneScale(dimensions: Vec3) {
  const largest = Math.max(...dimensions)
  return largest > 0 ? 1 / largest : 1
}

/** Map an engine-space cuboid (corner + oriented dimensions, cm) to a scene-space box. */
export function toSceneBox(position: Vec3, dimensions: Vec3, container: Vec3, scale = sceneScale(container)): SceneBox {
  const [x, y, z] = position, [l, w, h] = dimensions, [length, width] = container
  return {
    center: [(x + l / 2 - length / 2) * scale, (z + h / 2) * scale, -(y + w / 2 - width / 2) * scale],
    size: [l * scale, h * scale, w * scale],
  }
}

export function containerSceneBox(box: Pick<PackedBox, 'dimensions_cm'>): SceneBox {
  return toSceneBox([0, 0, 0], box.dimensions_cm, box.dimensions_cm)
}

export function placementSceneBox(placement: Placement, box: Pick<PackedBox, 'dimensions_cm'>): SceneBox {
  return toSceneBox(placement.position, placement.dimensions, box.dimensions_cm)
}

/** Distinct placement base heights, ascending; these are the options of the height slice filter. */
export function heightLevels(box: Pick<PackedBox, 'placements'>) {
  return [...new Set(box.placements.map(p => p.position[2]))].sort((a, b) => a - b)
}

/** Same rule as the original viewer: a unit is in the slice when its height range contains the level. */
export function isInHeightLayer(placement: Placement, layer: HeightLayer) {
  if (layer === 'all') return true
  const bottom = placement.position[2], top = bottom + placement.dimensions[2]
  return bottom <= layer && top > layer
}

/** Camera distance that fits the whole container inside a vertical field of view (degrees). */
export function fitDistance(box: Pick<PackedBox, 'dimensions_cm'>, fovDegrees: number, margin = 0.95) {
  const [sx, sy, sz] = containerSceneBox(box).size
  const radius = Math.hypot(sx, sy, sz) / 2
  return (radius / Math.sin((fovDegrees * Math.PI) / 360)) * margin
}

export const ZOOM_MIN = 0.5
export const ZOOM_MAX = 2

/** Imperative commands the viewer toolbar sends to the scene. */
export interface BoxSceneHandle {
  rotate(degrees: number): void
  zoomBy(delta: number): void
  reset(): void
}
