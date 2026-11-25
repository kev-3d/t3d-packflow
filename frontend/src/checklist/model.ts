// Shipment checklist: data model, reducer and pure selectors.
// No runtime imports on purpose — the module stays framework-free and testable on its own.

/** Forwarders used on the Export sheet, in display order. Any other value still works. */
export const KNOWN_CARRIERS = ['DHL', 'DHL insured', 'TNT', 'UPS', 'MP', 'Shiplowcost', 'Other'] as const
/** A forwarder name; '' when the shipment has none yet. */
export type Carrier = string
export type PackageStatus = 'por_empacar' | 'empacado' | 'retirado'
export type PickupSlot = 'Morning' | 'Afternoon'
export const SLOTS: PickupSlot[] = ['Morning', 'Afternoon']

/** One shipment on the board (a finished PO, or a shipment entered on the Export page). */
export type Package = {
  /** Shipment id. */
  id: number
  orderId: number | null
  po: string
  tracking: string
  carrier: Carrier
  customer: string
  /** ISO country code ('' when unknown). */
  destination: string
  items: number
  /** Box codes from the latest packing plan, one per box. */
  boxes: string[]
  status: PackageStatus
  /** Local calendar day, `YYYY-MM-DD`. */
  pickupDate: string | null
  pickupSlot: PickupSlot | null
  /** ISO timestamp set when the carrier collected it. */
  pickedUpAt: string | null
}

/* ---------- API mapping (the server speaks English) ---------- */

export type ApiPackStatus = 'to_pack' | 'packed' | 'picked_up'
export type ApiPickupSlot = 'morning' | 'afternoon'

export type ApiChecklistPackage = {
  id: number
  order_id: number | null
  po: string
  tracking: string
  carrier: string
  customer: string
  destination: string
  items: number
  boxes: string[]
  status: ApiPackStatus
  pickup_date: string | null
  pickup_slot: ApiPickupSlot | null
  picked_up_at: string | null
}

const STATUS_FROM_API: Record<ApiPackStatus, PackageStatus> = { to_pack: 'por_empacar', packed: 'empacado', picked_up: 'retirado' }
const SLOT_FROM_API: Record<ApiPickupSlot, PickupSlot> = { morning: 'Morning', afternoon: 'Afternoon' }

export function slotToApi(slot: PickupSlot | null): ApiPickupSlot | null {
  return slot === 'Morning' ? 'morning' : slot === 'Afternoon' ? 'afternoon' : null
}

export function packageFromApi(row: ApiChecklistPackage): Package {
  return {
    id: row.id,
    orderId: row.order_id,
    po: row.po,
    tracking: row.tracking,
    carrier: row.carrier,
    customer: row.customer,
    destination: row.destination,
    items: row.items,
    boxes: row.boxes,
    status: STATUS_FROM_API[row.status] ?? 'por_empacar',
    pickupDate: row.pickup_date,
    pickupSlot: row.pickup_slot ? SLOT_FROM_API[row.pickup_slot] ?? null : null,
    pickedUpAt: row.picked_up_at,
  }
}

/** The card title: the tracking number, or the PO while the shipment has none. */
export function packageTitle(pkg: Pick<Package, 'tracking' | 'po' | 'id'>): string {
  return pkg.tracking || pkg.po || `Shipment ${pkg.id}`
}

export function carrierLabel(carrier: Carrier): string {
  return carrier.trim() || 'No carrier'
}

/** Carriers present on the board, known forwarders first. */
export function selectCarriers(packages: Package[]): Carrier[] {
  const present = new Set(packages.filter(pkg => pkg.status !== 'retirado').map(pkg => pkg.carrier))
  return [...present].sort((a, b) => carrierRank(a) - carrierRank(b) || a.localeCompare(b))
}

function carrierRank(carrier: Carrier): number {
  if (!carrier) return 1000
  const index = (KNOWN_CARRIERS as readonly string[]).indexOf(carrier)
  return index === -1 ? 500 : index
}

export type Urgency = 'late' | 'urgent' | null

export type Filters = {
  query: string
  carriers: Carrier[]
  onlyUrgent: boolean
}

export const EMPTY_FILTERS: Filters = { query: '', carriers: [], onlyUrgent: false }

/* ---------- dates (local calendar days, never UTC) ---------- */

export function toIsoDate(date: Date): string {
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${m}-${d}`
}

export function parseIsoDate(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d)
}

export function addDays(iso: string, days: number): string {
  const date = parseIsoDate(iso)
  date.setDate(date.getDate() + days)
  return toIsoDate(date)
}

export function daysBetween(fromIso: string, toIso: string): number {
  return Math.round((parseIsoDate(toIso).getTime() - parseIsoDate(fromIso).getTime()) / 86_400_000)
}

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1)
const weekdayLong = new Intl.DateTimeFormat('en-US', { weekday: 'long' })
const weekdayShort = new Intl.DateTimeFormat('en-US', { weekday: 'short' })
const dayMonth = new Intl.DateTimeFormat('en-US', { day: 'numeric', month: 'short' })
const longDate = new Intl.DateTimeFormat('en-US', { weekday: 'long', day: 'numeric', month: 'long' })

const stripDot = (text: string) => text.replace(/\./g, '')

/** "Today", "Tomorrow", or the weekday name, relative to `today`. */
export function dayLabel(iso: string, today: string): string {
  const diff = daysBetween(today, iso)
  if (diff === 0) return 'Today'
  if (diff === 1) return 'Tomorrow'
  if (diff === -1) return 'Yesterday'
  return capitalize(weekdayLong.format(parseIsoDate(iso)))
}

/** "24 nov" */
export function shortDate(iso: string): string {
  return stripDot(dayMonth.format(parseIsoDate(iso)))
}

/** "Today", or "Wed 25" — compact and unambiguous next to a time slot. */
export function compactDayLabel(iso: string, today: string): string {
  if (iso === today) return 'Today'
  const date = parseIsoDate(iso)
  return `${capitalize(stripDot(weekdayShort.format(date)))} ${date.getDate()}`
}

export function longDateLabel(iso: string): string {
  return capitalize(longDate.format(parseIsoDate(iso)))
}

/* ---------- reducer ---------- */

export type ChecklistState = { packages: Package[] }

// The UI applies each action at once (optimistic) and then stores the server's copy with UPSERT.
export type ChecklistAction =
  | { type: 'LOAD'; packages: Package[] }
  | { type: 'UPSERT'; package: Package }
  | { type: 'MARK_PACKED'; id: number }
  | { type: 'MARK_UNPACKED'; id: number }
  | { type: 'ASSIGN_PICKUP'; id: number; date: string; slot: PickupSlot | null }
  | { type: 'CONFIRM_PICKUP'; id: number; at: string }
  | { type: 'UNDO_PICKUP'; id: number }

function update(state: ChecklistState, id: number, guard: (pkg: Package) => boolean, patch: Partial<Package>): ChecklistState {
  let changed = false
  const packages = state.packages.map(pkg => {
    if (pkg.id !== id || !guard(pkg)) return pkg
    changed = true
    return { ...pkg, ...patch }
  })
  return changed ? { packages } : state
}

export function checklistReducer(state: ChecklistState, action: ChecklistAction): ChecklistState {
  switch (action.type) {
    case 'LOAD':
      return { packages: action.packages }
    case 'UPSERT': {
      const exists = state.packages.some(pkg => pkg.id === action.package.id)
      return {
        packages: exists
          ? state.packages.map(pkg => (pkg.id === action.package.id ? action.package : pkg))
          : [...state.packages, action.package],
      }
    }
    case 'MARK_PACKED':
      return update(state, action.id, pkg => pkg.status === 'por_empacar', { status: 'empacado' })
    case 'MARK_UNPACKED':
      return update(state, action.id, pkg => pkg.status === 'empacado', { status: 'por_empacar' })
    case 'ASSIGN_PICKUP':
      return update(state, action.id, pkg => pkg.status !== 'retirado', { pickupDate: action.date, pickupSlot: action.slot })
    case 'CONFIRM_PICKUP':
      // A carrier can only collect what is already packed.
      return update(state, action.id, pkg => pkg.status === 'empacado', { status: 'retirado', pickedUpAt: action.at })
    case 'UNDO_PICKUP':
      return update(state, action.id, pkg => pkg.status === 'retirado', { status: 'empacado', pickedUpAt: null })
  }
}

/* ---------- urgency ---------- */

export function urgencyOf(pkg: Package, today: string): Urgency {
  if (pkg.status === 'retirado' || !pkg.pickupDate) return null
  if (pkg.pickupDate < today) return 'late'
  if (pkg.pickupDate === today && pkg.status === 'por_empacar') return 'urgent'
  return null
}

const urgencyRank = (u: Urgency) => (u === 'late' ? 0 : u === 'urgent' ? 1 : 2)

/** Late first, then urgent, then earliest pickup, unscheduled last. */
export function comparePackages(today: string) {
  return (a: Package, b: Package) =>
    urgencyRank(urgencyOf(a, today)) - urgencyRank(urgencyOf(b, today)) ||
    (a.pickupDate ?? '9999').localeCompare(b.pickupDate ?? '9999') ||
    (a.status === b.status ? 0 : a.status === 'por_empacar' ? -1 : 1) ||
    a.id - b.id
}

/* ---------- filters and search ---------- */

/** Lowercase and strip accents while keeping one output char per input char (so match indexes map back). */
export function foldText(text: string): string {
  return Array.from(text, ch => (ch.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase()[0] ?? ch)).join('')
}

export function normalizeQuery(query: string): string {
  return foldText(query.trim())
}

export function matchesQuery(pkg: Package, query: string): boolean {
  const q = normalizeQuery(query)
  if (!q) return true
  return [pkg.tracking, pkg.po, pkg.customer].some(field => foldText(field).includes(q))
}

export function matchesFilters(pkg: Package, filters: Filters, today: string): boolean {
  if (filters.carriers.length && !filters.carriers.includes(pkg.carrier)) return false
  if (filters.onlyUrgent && !urgencyOf(pkg, today)) return false
  return matchesQuery(pkg, filters.query)
}

export function hasActiveFilters(filters: Filters): boolean {
  return Boolean(filters.query.trim() || filters.carriers.length || filters.onlyUrgent)
}

export type TextSegment = { text: string; match: boolean }

/** Splits `text` around every case/accent-insensitive occurrence of `query`. */
export function splitMatches(text: string, query: string): TextSegment[] {
  const q = normalizeQuery(query)
  if (!q) return [{ text, match: false }]
  const chars = Array.from(text)
  const folded = foldText(text)
  const segments: TextSegment[] = []
  let cursor = 0
  let at = folded.indexOf(q)
  while (at !== -1) {
    if (at > cursor) segments.push({ text: chars.slice(cursor, at).join(''), match: false })
    segments.push({ text: chars.slice(at, at + q.length).join(''), match: true })
    cursor = at + q.length
    at = folded.indexOf(q, cursor)
  }
  if (cursor < chars.length) segments.push({ text: chars.slice(cursor).join(''), match: false })
  return segments
}

/* ---------- selectors ---------- */

export type KanbanView = { porEmpacar: Package[]; empacados: Package[] }

export function selectKanban(packages: Package[], filters: Filters, today: string): KanbanView {
  const visible = packages.filter(pkg => pkg.status !== 'retirado' && matchesFilters(pkg, filters, today)).sort(comparePackages(today))
  return {
    porEmpacar: visible.filter(pkg => pkg.status === 'por_empacar'),
    empacados: visible.filter(pkg => pkg.status === 'empacado'),
  }
}

export type CarrierGroupView = {
  key: string
  carrier: Carrier
  slot: PickupSlot | null
  packages: Package[]
}

export type DayView = {
  key: string
  kind: 'late' | 'day' | 'later'
  /** Set for `day` columns: the only ones that accept a pickup assignment. */
  date: string | null
  title: string
  subtitle: string
  groups: CarrierGroupView[]
  total: number
  unpacked: number
}

const slotRank = (slot: PickupSlot | null) => (slot === 'Morning' ? 0 : slot === 'Afternoon' ? 1 : 2)

export function groupByCarrierSlot(packages: Package[], today: string): CarrierGroupView[] {
  const groups = new Map<string, CarrierGroupView>()
  for (const pkg of packages) {
    const key = `${pkg.carrier}|${pkg.pickupSlot ?? 'none'}`
    const group = groups.get(key) ?? { key, carrier: pkg.carrier, slot: pkg.pickupSlot, packages: [] }
    group.packages.push(pkg)
    groups.set(key, group)
  }
  const sortPackages = comparePackages(today)
  return [...groups.values()]
    .map(group => ({ ...group, packages: group.packages.sort(sortPackages) }))
    .sort((a, b) => slotRank(a.slot) - slotRank(b.slot) || carrierRank(a.carrier) - carrierRank(b.carrier) || a.carrier.localeCompare(b.carrier))
}

function dayView(key: string, kind: DayView['kind'], date: string | null, title: string, subtitle: string, packages: Package[], today: string): DayView {
  return {
    key, kind, date, title, subtitle,
    groups: groupByCarrierSlot(packages, today),
    total: packages.length,
    unpacked: packages.filter(pkg => pkg.status === 'por_empacar').length,
  }
}

/** Carriers do not collect on Saturdays or Sundays. */
export function isShippingDay(iso: string): boolean {
  const weekday = parseIsoDate(iso).getDay()
  return weekday !== 0 && weekday !== 6
}

/** The next `count` shipping days (Monday–Friday), starting today when today is one. */
export function shippingDays(today: string, count = 5): string[] {
  const days: string[] = []
  for (let date = today; days.length < count; date = addDays(date, 1)) {
    if (isShippingDay(date)) days.push(date)
  }
  return days
}

/**
 * The pickup grid: one column per shipping day (Monday–Friday) for the next `horizon` of them.
 * An "Overdue" column (past dates) and a "Later" column (beyond the horizon) appear only while
 * some unfiltered package would land in them, so filtering never makes columns jump in and out.
 * A weekend day gets a column only if a package is already scheduled on it, so nothing is hidden.
 */
export function selectSchedule(packages: Package[], filters: Filters, today: string, horizon = 5): DayView[] {
  const scheduled = packages.filter(pkg => pkg.pickupDate && pkg.status !== 'retirado')
  const days = shippingDays(today, horizon)
  const last = days[days.length - 1]
  const visible = scheduled.filter(pkg => matchesFilters(pkg, filters, today))
  const columns: DayView[] = []

  if (scheduled.some(pkg => pkg.pickupDate! < today)) {
    columns.push(dayView('late', 'late', null, 'Overdue', 'Past pickup date', visible.filter(pkg => pkg.pickupDate! < today), today))
  }
  const weekendInUse = scheduled.map(pkg => pkg.pickupDate!).filter(date => date >= today && date <= last && !isShippingDay(date))
  for (const date of [...new Set([...days, ...weekendInUse])].sort()) {
    columns.push(dayView(date, 'day', date, dayLabel(date, today), shortDate(date), visible.filter(pkg => pkg.pickupDate === date), today))
  }
  if (scheduled.some(pkg => pkg.pickupDate! > last)) {
    columns.push(dayView('later', 'later', null, 'Later', `After ${shortDate(last)}`, visible.filter(pkg => pkg.pickupDate! > last), today))
  }
  return columns
}

export type Summary = { pendingPacking: number; pickupsToday: number; pickedUpToday: number }

/** Global counters: deliberately ignore filters. */
export function selectSummary(packages: Package[], today: string): Summary {
  return {
    pendingPacking: packages.filter(pkg => pkg.status === 'por_empacar').length,
    pickupsToday: packages.filter(pkg => pkg.pickupDate === today && pkg.status !== 'retirado').length,
    pickedUpToday: packages.filter(pkg => pkg.status === 'retirado' && pkg.pickedUpAt && toIsoDate(new Date(pkg.pickedUpAt)) === today).length,
  }
}

export function countVisible(packages: Package[], filters: Filters, today: string): { visible: number; total: number } {
  const active = packages.filter(pkg => pkg.status !== 'retirado')
  return { visible: active.filter(pkg => matchesFilters(pkg, filters, today)).length, total: active.length }
}

export type PickupOption = { value: string; label: string; date: string; slot: PickupSlot }

/** Day × slot choices for the pickup selector: the next shipping days (Monday–Friday). */
export function pickupOptions(today: string, horizon = 5): PickupOption[] {
  const options: PickupOption[] = []
  for (const date of shippingDays(today, horizon)) {
    for (const slot of SLOTS) options.push({ value: `${date}|${slot}`, label: `${compactDayLabel(date, today)} · ${slot}`, date, slot })
  }
  return options
}

export function pickupValue(pkg: Pick<Package, 'pickupDate' | 'pickupSlot'>): string {
  return pkg.pickupDate ? `${pkg.pickupDate}|${pkg.pickupSlot ?? ''}` : ''
}

export function parsePickupValue(value: string): { date: string; slot: PickupSlot | null } | null {
  const [date, slot] = value.split('|')
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date ?? '')) return null
  return { date, slot: slot === 'Morning' || slot === 'Afternoon' ? slot : null }
}

/** "24 items · 2 boxes (B3, B5)" — leaves out whatever the shipment does not know. */
export function boxLabel(pkg: Pick<Package, 'items' | 'boxes'>): string {
  const parts: string[] = []
  if (pkg.items > 0) parts.push(`${pkg.items} ${pkg.items === 1 ? 'item' : 'items'}`)
  if (pkg.boxes.length) {
    const codes = [...new Set(pkg.boxes.filter(Boolean))].join(', ')
    parts.push(`${pkg.boxes.length} ${pkg.boxes.length === 1 ? 'box' : 'boxes'}${codes ? ` (${codes})` : ''}`)
  }
  return parts.join(' · ') || 'No packing plan'
}

const regionNames = (() => {
  try { return new Intl.DisplayNames(['en'], { type: 'region' }) } catch { return null }
})()

/** "Germany" from "DE"; the code itself if the browser does not know it. */
export function countryName(code: string): string {
  if (!code) return 'No destination'
  try { return regionNames?.of(code.toUpperCase()) ?? code } catch { return code }
}

/* ---------- Overview card: one tab per pickup day ---------- */

export type DayTab = {
  key: string
  kind: DayView['kind'] | 'unscheduled'
  title: string
  subtitle: string
  toPack: Package[]
  packed: Package[]
  /** Carrier · slot blocks of the day, with every package still to collect. */
  pickups: CarrierGroupView[]
  /** Already collected for this day. */
  pickedUp: number
}

/**
 * The checklist cut into day tabs: the same columns as the pickup grid (Overdue, Today … , Later),
 * plus "No date" for packages that have no pickup day yet. Each tab lists what is still to pack,
 * what is packed, and the pickups by carrier and slot.
 */
export function selectDayTabs(packages: Package[], today: string, horizon = 5): DayTab[] {
  const sort = comparePackages(today)
  const tabs: DayTab[] = selectSchedule(packages, EMPTY_FILTERS, today, horizon).map(day => {
    const inDay = day.groups.flatMap(group => group.packages).sort(sort)
    const last = shippingDays(today, horizon)[horizon - 1]
    const pickedUp = packages.filter(pkg => pkg.status === 'retirado' && pkg.pickupDate && (
      day.kind === 'day' ? pkg.pickupDate === day.date : day.kind === 'late' ? pkg.pickupDate < today : pkg.pickupDate > last
    )).length
    return {
      key: day.key, kind: day.kind, title: day.title, subtitle: day.subtitle,
      toPack: inDay.filter(pkg => pkg.status === 'por_empacar'),
      packed: inDay.filter(pkg => pkg.status === 'empacado'),
      pickups: day.groups,
      // An overdue pickup was collected late: it counts on the day it was picked up, not here.
      pickedUp: day.kind === 'late' ? 0 : pickedUp,
    }
  })
  const unscheduled = packages.filter(pkg => !pkg.pickupDate && pkg.status !== 'retirado').sort(sort)
  if (unscheduled.length) {
    tabs.push({
      key: 'unscheduled', kind: 'unscheduled', title: 'No date', subtitle: 'Not scheduled',
      toPack: unscheduled.filter(pkg => pkg.status === 'por_empacar'),
      packed: unscheduled.filter(pkg => pkg.status === 'empacado'),
      pickups: [], pickedUp: 0,
    })
  }
  return tabs
}
