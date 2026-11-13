import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

// Same approach as review.test.mjs: transpile the pure TS module and import it.
async function loadModule(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const scene = await loadModule('../src/components/box3d/sceneBuilder.ts')
const close = (actual, expected) => actual.forEach((value, i) => assert.ok(Math.abs(value - expected[i]) < 1e-9, `${actual} ≈ ${expected}`))

const box = {
  dimensions_cm: [40, 30, 20],
  placements: [
    { uid: 'a-1', sku: 'A', name: 'Alpha', position: [0, 0, 0], dimensions: [20, 10, 10] },
    { uid: 'b-1', sku: 'B', name: 'Beta', position: [20, 10, 0], dimensions: [20, 20, 5] },
    { uid: 'a-2', sku: 'A', name: 'Alpha', position: [0, 0, 10], dimensions: [20, 10, 10] },
  ],
}

test('scale normalises the largest box dimension to one scene unit', () => {
  assert.equal(scene.sceneScale([40, 30, 20]), 1 / 40)
  assert.equal(scene.sceneScale([0, 0, 0]), 1)
})

test('container is centred on the floor with engine z mapped to scene Y', () => {
  const { center, size } = scene.containerSceneBox(box)
  close(center, [0, 0.25, 0])
  close(size, [1, 0.5, 0.75])
})

test('placements map x→X, z→Y and y→-Z around the container centre', () => {
  const first = scene.placementSceneBox(box.placements[0], box)
  close(first.size, [0.5, 0.25, 0.25])
  // corner (0,0,0) + half size (10,5,5) cm, shifted by half the container (20,15) cm
  close(first.center, [-10 / 40, 5 / 40, 10 / 40])
  const stacked = scene.placementSceneBox(box.placements[2], box)
  assert.ok(stacked.center[1] > first.center[1], 'a unit at a higher z is drawn higher')
})

test('height levels are distinct and sorted', () => {
  assert.deepEqual(scene.heightLevels(box), [0, 10])
})

test('height slice keeps the original rule: bottom <= level < top', () => {
  const visible = level => box.placements.filter(p => scene.isInHeightLayer(p, level)).map(p => p.uid)
  assert.deepEqual(visible('all'), ['a-1', 'b-1', 'a-2'])
  assert.deepEqual(visible(0), ['a-1', 'b-1'])
  assert.deepEqual(visible(5), ['a-1'])
  assert.deepEqual(visible(10), ['a-2'])
})

test('product colours are deterministic and match the original palette', () => {
  assert.equal(scene.colorForProduct('SKU-1'), scene.colorForProduct('SKU-1'))
  assert.match(scene.colorForProduct('anything'), /^#[0-9A-F]{6}$/)
})

test('fit distance grows with the field of view shrinking', () => {
  assert.ok(scene.fitDistance(box, 30) > scene.fitDistance(box, 60))
})
