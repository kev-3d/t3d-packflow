import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

async function loadModule(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const model = await loadModule('../src/checklist/model.ts')
const {
  checklistReducer, selectKanban, selectSchedule, selectSummary, urgencyOf, toIsoDate, addDays,
  dayLabel, splitMatches, matchesFilters, EMPTY_FILTERS, pickupOptions, packageFromApi, slotToApi,
  selectCarriers, boxLabel, packageTitle,
} = model

const now = new Date(2026, 10, 24, 10, 0) // Tuesday
const today = toIsoDate(now)

// Test fixtures in the server's shape (GET /api/checklist).
let nextId = 1
const row = (overrides, dayOffset = null) => ({
  id: nextId++, order_id: 1, po: 'PO-1', tracking: '', carrier: 'DHL', customer: 'Cliente', destination: 'DE',
  items: 3, boxes: ['B3'], status: 'to_pack', pickup_date: dayOffset === null ? null : addDays(today, dayOffset),
  pickup_slot: dayOffset === null ? null : 'afternoon', picked_up_at: null, ...overrides,
})
const fixtures = () => {
  nextId = 1
  return [
    row({ tracking: '4829174630', po: 'PO-4521', customer: 'Ferretería Ortega' }, 0),   // 1 urgent
    row({ status: 'packed', tracking: '4829174652' }, 0),                                 // 2
    row({ status: 'packed', tracking: '4829174688' }, 0),                                 // 3
    row({ status: 'packed', tracking: '4829174695' }, 0),                                 // 4 -> DHL · Afternoon block of 4
    row({ carrier: 'UPS', status: 'packed', tracking: '1Z84V3010355551212', boxes: [] }), // 5 packed, no day
    row({ carrier: 'TNT', status: 'packed' }, -1),                                        // 6 late
    row({ carrier: '', po: 'PO-9' }, 2),                                                  // 7 no carrier yet
    row({ carrier: 'UPS', status: 'picked_up', picked_up_at: now.toISOString() }, 0),    // 8 picked up today
  ].map(packageFromApi)
}
const fresh = () => ({ packages: fixtures() })
const byId = (state, id) => state.packages.find(p => p.id === id)

test('server rows map to board packages', () => {
  const [first, , , , , , noCarrier, pickedUp] = fixtures()
  assert.equal(first.status, 'por_empacar')
  assert.equal(first.pickupSlot, 'Afternoon')
  assert.equal(pickedUp.status, 'retirado')
  assert.equal(slotToApi('Morning'), 'morning')
  assert.equal(packageTitle(noCarrier), 'PO-9')
  assert.deepEqual(selectCarriers(fixtures()), ['DHL', 'TNT', 'UPS', ''])
  assert.equal(boxLabel({ items: 24, boxes: ['B3', 'B5', 'B3'] }), '24 items · 3 boxes (B3, B5)')
  assert.equal(boxLabel({ items: 0, boxes: [] }), 'No packing plan')
})

test('a reload replaces the board and UPSERT stores the server copy', () => {
  const loaded = checklistReducer({ packages: [] }, { type: 'LOAD', packages: fixtures() })
  assert.equal(loaded.packages.length, 8)
  const server = { ...byId(loaded, 1), status: 'empacado' }
  const upserted = checklistReducer(loaded, { type: 'UPSERT', package: server })
  assert.equal(byId(upserted, 1).status, 'empacado')
  assert.equal(upserted.packages.length, 8)
  const added = checklistReducer(loaded, { type: 'UPSERT', package: { ...server, id: 99 } })
  assert.equal(added.packages.length, 9)
})

test('marking packed moves the card and updates derived counters', () => {
  const before = fresh()
  const after = checklistReducer(before, { type: 'MARK_PACKED', id: 1 })
  assert.equal(byId(after, 1).status, 'empacado')
  const k0 = selectKanban(before.packages, EMPTY_FILTERS, today)
  const k1 = selectKanban(after.packages, EMPTY_FILTERS, today)
  assert.equal(k1.porEmpacar.length, k0.porEmpacar.length - 1)
  assert.equal(k1.empacados.length, k0.empacados.length + 1)
  assert.equal(selectSummary(after.packages, today).pendingPacking, selectSummary(before.packages, today).pendingPacking - 1)
  assert.equal(urgencyOf(byId(after, 1), today), null)
})

test('assigning a day places the card in that day column, also kept in Empacados', () => {
  const date = addDays(today, 2)
  const state = checklistReducer(fresh(), { type: 'ASSIGN_PICKUP', id: 5, date, slot: 'Afternoon' })
  const day = selectSchedule(state.packages, EMPTY_FILTERS, today).find(d => d.date === date)
  assert.ok(day.groups.some(g => g.carrier === 'UPS' && g.slot === 'Afternoon' && g.packages.some(p => p.id === 5)))
  assert.ok(selectKanban(state.packages, EMPTY_FILTERS, today).empacados.some(p => p.id === 5))
  const block = selectSchedule(fresh().packages, EMPTY_FILTERS, today).find(d => d.date === today).groups.find(g => g.carrier === 'DHL')
  assert.equal(block.packages.length, 4)
})

test('urgency: unpacked today is urgent, overdue is late', () => {
  const base = { status: 'por_empacar', pickupDate: today }
  assert.equal(urgencyOf(base, today), 'urgent')
  assert.equal(urgencyOf({ ...base, status: 'empacado' }, today), null)
  assert.equal(urgencyOf({ ...base, pickupDate: addDays(today, -1) }, today), 'late')
  assert.equal(urgencyOf({ ...base, status: 'empacado', pickupDate: addDays(today, -1) }, today), 'late')
  assert.equal(urgencyOf({ ...base, status: 'retirado', pickupDate: addDays(today, -1) }, today), null)
})

test('pickup cannot be confirmed unless packed; undo restores it', () => {
  const s0 = fresh()
  assert.equal(checklistReducer(s0, { type: 'CONFIRM_PICKUP', id: 1, at: now.toISOString() }), s0)
  const s1 = checklistReducer(s0, { type: 'CONFIRM_PICKUP', id: 2, at: now.toISOString() })
  assert.equal(byId(s1, 2).status, 'retirado')
  assert.equal(selectSummary(s1.packages, today).pickedUpToday, selectSummary(s0.packages, today).pickedUpToday + 1)
  assert.ok(!selectKanban(s1.packages, EMPTY_FILTERS, today).empacados.some(p => p.id === 2))
  const s2 = checklistReducer(s1, { type: 'UNDO_PICKUP', id: 2 })
  assert.equal(byId(s2, 2).status, 'empacado')
})

test('filters narrow both zones; global summary ignores them', () => {
  const { packages } = fresh()
  const filters = { query: '', carriers: ['UPS'], onlyUrgent: false }
  const k = selectKanban(packages, filters, today)
  assert.ok([...k.porEmpacar, ...k.empacados].every(p => p.carrier === 'UPS'))
  assert.ok(selectSchedule(packages, filters, today).flatMap(d => d.groups).every(g => g.carrier === 'UPS'))
  const urgentOnly = selectKanban(packages, { ...EMPTY_FILTERS, onlyUrgent: true }, today)
  assert.ok([...urgentOnly.porEmpacar, ...urgentOnly.empacados].every(p => urgencyOf(p, today)))
})

test('search matches tracking, order and customer, accent-insensitive', () => {
  const pkg = { tracking: '4829174630', po: 'PO-4521', customer: 'Ferretería Ortega', carrier: 'DHL', status: 'por_empacar', pickupDate: null }
  for (const query of ['48291746', 'po-4521', 'ferreteria']) assert.ok(matchesFilters(pkg, { ...EMPTY_FILTERS, query }, today), query)
  assert.ok(!matchesFilters(pkg, { ...EMPTY_FILTERS, query: 'xyz' }, today))
  assert.deepEqual(splitMatches('Ferretería Ortega', 'eria'), [
    { text: 'Ferret', match: false }, { text: 'ería', match: true }, { text: ' Ortega', match: false },
  ])
})

test('day labels and pickup options are relative to today', () => {
  assert.equal(dayLabel(today, today), 'Today')
  assert.equal(dayLabel(addDays(today, 1), today), 'Tomorrow')
  assert.match(dayLabel(addDays(today, 2), today), /^Thursday$/)
  const options = pickupOptions(today)
  assert.equal(options.length, 10)
  assert.equal(options[0].label, 'Today · Morning')
  const cols = selectSchedule(fixtures(), EMPTY_FILTERS, today)
  assert.equal(cols[0].kind, 'late')
  assert.deepEqual(cols.filter(c => c.kind === 'day').map(c => c.date), [0, 1, 2, 3, 6].map(n => addDays(today, n)))
})

test('overview day tabs split each day into to pack, packed and pickups', () => {
  const tabs = model.selectDayTabs(fixtures(), today)
  assert.deepEqual(tabs.map(t => t.title), ['Overdue', 'Today', 'Tomorrow', 'Thursday', 'Friday', 'Monday', 'No date'])
  const todayTab = tabs.find(t => t.key === today)
  assert.deepEqual(todayTab.toPack.map(p => p.id), [1])
  assert.deepEqual(todayTab.packed.map(p => p.id), [2, 3, 4])
  assert.deepEqual(todayTab.pickups.map(g => [g.carrier, g.slot, g.packages.length]), [['DHL', 'Afternoon', 4]])
  assert.equal(todayTab.pickedUp, 1)
  assert.deepEqual(tabs.find(t => t.kind === 'late').packed.map(p => p.id), [6])
  assert.deepEqual(tabs.find(t => t.kind === 'unscheduled').packed.map(p => p.id), [5])
})

test('pickups only on weekdays: weekends are skipped unless a package is already on one', () => {
  const friday = '2026-11-27'
  assert.deepEqual(model.shippingDays(friday), ['2026-11-27', '2026-11-30', '2026-12-01', '2026-12-02', '2026-12-03'])
  assert.deepEqual(model.shippingDays('2026-11-28', 1), ['2026-11-30']) // Saturday -> Monday
  assert.ok(pickupOptions(friday).every(option => model.isShippingDay(option.date)))
  const days = pkgs => selectSchedule(pkgs, EMPTY_FILTERS, friday).filter(c => c.kind === 'day').map(c => c.title)
  assert.deepEqual(days([]), ['Today', 'Monday', 'Tuesday', 'Wednesday', 'Thursday'])
  // A package already scheduled for Saturday stays visible in its own column.
  const onSaturday = [packageFromApi({ ...row({ status: 'packed' }), pickup_date: '2026-11-28' })]
  assert.deepEqual(days(onSaturday), ['Today', 'Tomorrow', 'Monday', 'Tuesday', 'Wednesday', 'Thursday'])
})
