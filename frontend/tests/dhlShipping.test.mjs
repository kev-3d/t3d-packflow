import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

async function loadModule(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}
const { fuelFor, demandFor, initialInputs, quotePayload, canAutoQuote, formatMoney, numberOrNull, todayIso } = await loadModule('../src/dhlShipping.ts')

const config = {
  meta: { version: 'DHL-MT-2026', currency: 'EUR', origin: 'MT', valid_to: '2026-12-31', volumetric_divisor: 5000, notes: [] },
  services: [],
  destinations: [{ code: 'DE', name: 'Germany', zone: 1, demand_region: 'europe' }, { code: 'AF', name: 'Afghanistan', zone: 6, demand_region: 'rest_of_world' }],
  surcharges: {
    fuel: { weeks: [{ from: '2026-11-09', to: '2026-11-15', pct: 50 }, { from: '2026-11-16', to: '2026-11-22', pct: 49.5 }] },
    demand: { valid_from: '2026-10-01', valid_to: '2027-02-05', origin_region: 'Europe',
      rates: { china_hk: 0, asia: 0, oceania: 0.5, europe: 0.3, americas: 0.5, mena: 0, rest_of_world: 0.8 } },
    region_labels: { europe: 'Europe', rest_of_world: 'Rest of the World' },
    defaults: { vat_pct: 18, gogreen_per_kg: 0.2 },
  },
}

test('fuel follows the published DHL week of the shipment date', () => {
  assert.equal(fuelFor(config, '2026-11-13').pct, 50)
  assert.equal(fuelFor(config, '2026-11-16').pct, 49.5)
  assert.equal(fuelFor(config, '2026-11-23'), null)
})

test('demand depends on the destination region and the demand period', () => {
  assert.equal(demandFor(config, 'AF', '2026-11-13').perKg, 0.8)
  assert.equal(demandFor(config, 'DE', '2026-11-13').perKg, 0.3)
  assert.equal(demandFor(config, 'DE', '2026-09-30').perKg, 0)
  assert.equal(demandFor(config, 'DE', '2026-11-13').label, 'Europe')
})

test('initial inputs use the detected destination, or the saved quote with legacy sources', () => {
  const fresh = initialInputs(config, null, 'DE', '2026-11-13')
  assert.equal(fresh.destination, 'DE')
  assert.equal(fresh.fuel_source, 'dhl')
  assert.equal(fresh.vat_pct, 18)
  assert.equal(initialInputs(config, null, null, '2027-03-01').shipment_date, '2026-12-31')
  const saved = initialInputs(config, { inputs: { ...fresh, destination: 'AF', fuel_source: 'saved', demand_source: 'saved' } }, 'DE', '2026-11-13')
  assert.equal(saved.destination, 'AF')
  assert.equal(saved.fuel_source, 'dhl')
  assert.equal(saved.demand_source, 'dhl')
})

test('auto quote needs a destination and a fuel rate for the date', () => {
  const inputs = initialInputs(config, null, 'DE', '2026-11-13')
  assert.equal(canAutoQuote(inputs, config), true)
  assert.equal(canAutoQuote({ ...inputs, destination: '' }, config), false)
  assert.equal(canAutoQuote({ ...inputs, shipment_date: '2026-11-30' }, config), false)
  assert.equal(canAutoQuote({ ...inputs, shipment_date: '2026-11-30', fuel_source: 'manual', fuel_pct: 50 }, config), true)
  assert.equal(canAutoQuote({ ...inputs, demand_source: 'manual', demand_per_kg: null }, config), false)
})

test('payload drops values the server resolves from DHL schedules', () => {
  const inputs = { ...initialInputs(config, null, 'DE', '2026-11-13'), fuel_pct: 12, demand_source: 'manual', demand_per_kg: 0.3 }
  const payload = quotePayload(inputs)
  assert.equal(payload.fuel_pct, null)
  assert.equal(payload.demand_per_kg, 0.3)
})

test('formatting helpers', () => {
  assert.equal(formatMoney(35.35), 'EUR 35.35')
  assert.equal(formatMoney(1234.5, 'EUR'), 'EUR 1,234.50')
  assert.equal(numberOrNull(''), null)
  assert.equal(numberOrNull('0'), 0)
  assert.equal(numberOrNull('abc'), null)
  assert.equal(todayIso(new Date(2026, 10, 12, 23, 30)), '2026-11-12')
})
