import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

async function loadModule(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}
const { matchProducts, findCatalogProduct, applyCatalogProduct, matchClients, findClient, suggestedDeliveryAddress } = await loadModule('../src/manualOrder.ts')

const product = (id, name, price_eur = 0, active = 1) => ({ id, name, price_eur, active, length_cm: 1, width_cm: 1, height_cm: 1, weight_kg: 1, rotation_allowed: 1, fragile: 0 })
const catalog = [
  product(1, 'Magigoo Pro PA', 10),
  product(2, 'Glide kit Magigoo', 25),
  product(3, 'Válvula sanitaria', 4.5),
  product(4, 'Magigoo HT', 20),
  product(5, 'Magigoo retired', 9, 0),
]

test('matching is case/accent-insensitive, skips inactive products and ranks prefix matches first', () => {
  assert.deepEqual(matchProducts(catalog, 'magi').map(p => p.id), [4, 1, 2])
  assert.deepEqual(matchProducts(catalog, 'VALVULA').map(p => p.id), [3])
  assert.deepEqual(matchProducts(catalog, 'nothing'), [])
})

test('an empty query lists active products by name, up to the limit', () => {
  assert.deepEqual(matchProducts(catalog, '  ', 2).map(p => p.name), ['Glide kit Magigoo', 'Magigoo HT'])
})

test('exact catalog name is found regardless of case, accents and spacing', () => {
  assert.equal(findCatalogProduct(catalog, '  magigoo   pro pa ')?.id, 1)
  assert.equal(findCatalogProduct(catalog, 'valvula sanitaria')?.id, 3)
  assert.equal(findCatalogProduct(catalog, 'Magigoo Pro'), undefined)
  assert.equal(findCatalogProduct(catalog, 'Magigoo retired'), undefined)
  assert.equal(findCatalogProduct(catalog, ''), undefined)
})

test('picking a product fills the exact name and catalog price and keeps the typed quantity', () => {
  assert.deepEqual(applyCatalogProduct({ description: 'magi', quantity: 7, unit_price: 0 }, catalog[3]), { description: 'Magigoo HT', quantity: 7, unit_price: 20 })
  assert.equal(applyCatalogProduct({ description: 'x', quantity: 1, unit_price: 3 }, product(9, 'No price')).unit_price, 0)
})

const client = (id, customer, extra = {}) => ({ id, customer, phone_number: '', email: '', contact_name: '', address: '', country: '', shipping_address: '', created_at: '', updated_at: '', ...extra })
const clients = [
  client(1, 'Hava 3D', { country: 'France', shipping_address: '12 Rue Hippolyte, Le Mans', address: 'HQ address' }),
  client(2, 'Northstar Labs', { address: '1 Main St' }),
  client(3, 'Électronique Hava', { address: '' }),
]

test('client search is accent-insensitive and ranks names starting with the query first', () => {
  assert.deepEqual(matchClients(clients, 'hava').map(c => c.id), [1, 3])
  assert.deepEqual(matchClients(clients, 'electronique').map(c => c.id), [3])
  assert.deepEqual(matchClients(clients, '', 2).map(c => c.customer), ['Électronique Hava', 'Hava 3D'])
})

test('exact client match follows the backend rule: case and outer spaces ignored, accents and inner spaces not', () => {
  assert.equal(findClient(clients, '  hava 3d ')?.id, 1)
  assert.equal(findClient(clients, 'hava  3d'), undefined)
  assert.equal(findClient(clients, 'electronique hava'), undefined)
  assert.equal(findClient(clients, 'électronique hava')?.id, 3)
  assert.equal(findClient(clients, 'Hava'), undefined)
  assert.equal(findClient(clients, ''), undefined)
})

test('a picked client suggests its shipping address, else its address, and never replaces typed text', () => {
  assert.equal(suggestedDeliveryAddress('', clients[0]), '12 Rue Hippolyte, Le Mans')
  assert.equal(suggestedDeliveryAddress('  ', clients[1]), '1 Main St')
  assert.equal(suggestedDeliveryAddress('', clients[2]), '')
  assert.equal(suggestedDeliveryAddress('Typed address', clients[0]), 'Typed address')
})
