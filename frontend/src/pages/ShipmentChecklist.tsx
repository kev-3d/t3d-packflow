import { AlertCircle, LoaderCircle, SearchX } from 'lucide-react'
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { api, ApiError, type ChecklistActionInput } from '../api'
import { useDebouncedValue, useSlashShortcut, useToday, useViewportOffset } from '../checklist/hooks'
import {
  checklistReducer, countVisible, hasActiveFilters, longDateLabel, packageFromApi, packageTitle,
  pickupOptions as buildPickupOptions, selectCarriers, selectKanban, selectSchedule, selectSummary, slotToApi,
  type ChecklistAction, type Carrier, type Filters as FilterState, type Package,
} from '../checklist/model'
import { ChecklistContext, type ChecklistActions, type ChecklistContextValue } from '../components/checklist/context'
import { DayColumn } from '../components/checklist/DayColumn'
import { Filters, Header } from '../components/checklist/Header'
import { KanbanColumn } from '../components/checklist/KanbanColumn'
import { UndoToast } from '../components/checklist/UndoToast'
import '../checklist.css'

const UNDO_MS = 8000
/** Several people work the warehouse at once: reload the board this often while it is open. */
const REFRESH_MS = 30_000

type Props = { notify: (message: string, error?: boolean) => void }

export function ShipmentChecklist({ notify }: Props) {
  const today = useToday()
  const [state, dispatch] = useReducer(checklistReducer, { packages: [] })
  const packages = state.packages

  /* ---------- server sync ---------- */
  const [loadState, setLoadState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  // Changes are sent one after another (so "retirar" then "deshacer" arrive in order),
  // and a reload never overwrites the board while one of them is still on its way.
  const queue = useRef<Promise<unknown>>(Promise.resolve())
  const inFlight = useRef(0)

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setRefreshing(true)
    try {
      const rows = await api.checklist()
      if (inFlight.current === 0) {
        dispatch({ type: 'LOAD', packages: rows.map(packageFromApi) })
        setUpdatedAt(new Date())
      }
      setLoadState('ready')
    } catch (error) {
      setLoadState(current => (current === 'ready' ? current : 'error'))
      if (!quiet) notify(error instanceof Error ? error.message : 'The board could not be loaded.', true)
    } finally {
      if (!quiet) setRefreshing(false)
    }
  }, [notify])

  useEffect(() => {
    load(true)
    const timer = setInterval(() => { if (document.visibilityState === 'visible') load(true) }, REFRESH_MS)
    const onVisible = () => { if (document.visibilityState === 'visible') load(true) }
    document.addEventListener('visibilitychange', onVisible)
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', onVisible) }
  }, [load])

  /** Apply a change on screen at once, then save it; on failure the board reloads from the server. */
  const send = useCallback((pkg: Package, optimistic: ChecklistAction, input: ChecklistActionInput) => {
    dispatch(optimistic)
    inFlight.current += 1
    queue.current = queue.current.then(async () => {
      try {
        dispatch({ type: 'UPSERT', package: packageFromApi(await api.checklistAction(pkg.id, input)) })
      } catch (error) {
        notify(error instanceof ApiError && error.status === 409
          ? `${packageTitle(pkg)} was already changed by someone else. Board refreshed.`
          : `The change to ${packageTitle(pkg)} could not be saved. Please try again.`, true)
        inFlight.current -= 1
        await load(true)
        return
      }
      inFlight.current -= 1
    })
  }, [load, notify])

  /* ---------- filters ---------- */
  // The input updates instantly, the boards follow the debounced text.
  const [query, setQuery] = useState('')
  const debouncedQuery = useDebouncedValue(query, 150)
  const [carriers, setCarriers] = useState<Carrier[]>([])
  const [onlyUrgent, setOnlyUrgent] = useState(false)
  const filters = useMemo<FilterState>(() => ({ query: debouncedQuery, carriers, onlyUrgent }), [debouncedQuery, carriers, onlyUrgent])

  // Everything on screen is derived from `packages` — no counter is stored.
  const kanban = useMemo(() => selectKanban(packages, filters, today), [packages, filters, today])
  const schedule = useMemo(() => selectSchedule(packages, filters, today), [packages, filters, today])
  const summary = useMemo(() => selectSummary(packages, today), [packages, today])
  const counts = useMemo(() => countVisible(packages, filters, today), [packages, filters, today])
  const availableCarriers = useMemo(() => selectCarriers(packages), [packages])
  const pickupOptions = useMemo(() => buildPickupOptions(today), [today])
  const active = hasActiveFilters(filters) || query.trim() !== ''

  /* ---------- actions ---------- */
  const [announcement, setAnnouncement] = useState('')
  const [undo, setUndo] = useState<Package | null>(null)
  useEffect(() => {
    if (!undo) return
    const timer = setTimeout(() => setUndo(null), UNDO_MS)
    return () => clearTimeout(timer)
  }, [undo])

  const packagesRef = useRef(packages)
  packagesRef.current = packages
  const findPackage = useCallback((id: number) => packagesRef.current.find(pkg => pkg.id === id), [])

  const actions = useMemo<ChecklistActions>(() => ({
    markPacked: pkg => {
      send(pkg, { type: 'MARK_PACKED', id: pkg.id }, { action: 'pack' })
      setAnnouncement(`${packageTitle(pkg)} marked as packed.`)
    },
    markUnpacked: pkg => {
      send(pkg, { type: 'MARK_UNPACKED', id: pkg.id }, { action: 'unpack' })
      setAnnouncement(`${packageTitle(pkg)} moved back to To pack.`)
    },
    assignPickup: (pkg, date, slot) => {
      send(pkg, { type: 'ASSIGN_PICKUP', id: pkg.id, date, slot }, { action: 'assign_pickup', pickup_date: date, pickup_slot: slotToApi(slot) })
      const label = buildPickupOptions(today).find(option => option.date === date && option.slot === slot)?.label ?? date
      setAnnouncement(`${packageTitle(pkg)}: retiro asignado para ${label}.`)
    },
    confirmPickup: pkg => {
      if (pkg.status !== 'empacado') return
      send(pkg, { type: 'CONFIRM_PICKUP', id: pkg.id, at: new Date().toISOString() }, { action: 'pick_up' })
      setUndo(pkg)
      setAnnouncement(`${packageTitle(pkg)} picked up. You can undo it for a few seconds.`)
    },
  }), [send, today])

  const undoPickup = () => {
    if (!undo) return
    send(undo, { type: 'UNDO_PICKUP', id: undo.id }, { action: 'undo_pick_up' })
    setAnnouncement(`Pickup of ${packageTitle(undo)} undone.`)
    setUndo(null)
  }

  const clearFilters = () => {
    setQuery('')
    setCarriers([])
    setOnlyUrgent(false)
  }

  const context = useMemo<ChecklistContextValue>(
    () => ({ today, query: debouncedQuery, pickupOptions, actions, findPackage }),
    [today, debouncedQuery, pickupOptions, actions, findPackage],
  )

  const rootRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  useViewportOffset(rootRef)
  useSlashShortcut(searchRef)

  const noMatches = active && counts.visible === 0 && counts.total > 0
  const boardEmpty = loadState === 'ready' && counts.total === 0

  return (
    <ChecklistContext.Provider value={context}>
      <div className="ck" ref={rootRef}>
        <Header dateLabel={longDateLabel(today)} summary={summary} updatedAt={updatedAt} refreshing={refreshing} onRefresh={() => load()} />
        <Filters
          searchRef={searchRef}
          query={query}
          onQueryChange={setQuery}
          availableCarriers={availableCarriers}
          carriers={carriers}
          onToggleCarrier={carrier => setCarriers(list => (list.includes(carrier) ? list.filter(c => c !== carrier) : [...list, carrier]))}
          onlyUrgent={onlyUrgent}
          onToggleUrgent={() => setOnlyUrgent(value => !value)}
          onClear={clearFilters}
          active={active}
          visible={counts.visible}
          total={counts.total}
        />

        {noMatches && (
          <div className="ck-nomatch" role="status">
            <SearchX aria-hidden="true" size={18} />
            <span>{filters.query.trim() ? <>No packages match “{filters.query.trim()}”</> : 'No packages match the filters'}.</span>
            <button type="button" className="ck-btn ck-btn--ghost" onClick={clearFilters}>Clear filters</button>
          </div>
        )}

        {loadState === 'loading' && (
          <div className="ck-state" role="status"><LoaderCircle className="spin" aria-hidden="true" size={22} /><p>Loading shipments…</p></div>
        )}
        {loadState === 'error' && (
          <div className="ck-state" role="alert">
            <AlertCircle aria-hidden="true" size={22} />
            <p>The board could not be loaded. Check the connection to the server.</p>
            <button type="button" className="ck-btn ck-btn--ghost" onClick={() => { setLoadState('loading'); load() }}>Try again</button>
          </div>
        )}

        {loadState === 'ready' && (
          <div className="ck-zones">
            <section className="ck-zone ck-zone--kanban" aria-labelledby="ck-zone-pack">
              <h2 id="ck-zone-pack" className="ck-zone__title">Packing</h2>
              <div className="ck-kanban">
                <KanbanColumn
                  kind="por_empacar"
                  title="To pack"
                  packages={kanban.porEmpacar}
                  emptyMessage={boardEmpty
                    ? 'No pending shipments. Each PO appears here when you press Save & Finish.'
                    : active ? 'No packages to pack match the filters.' : 'Everything is packed. Nice work!'}
                />
                <KanbanColumn
                  kind="empacados"
                  title="Packed"
                  packages={kanban.empacados}
                  emptyMessage={active ? 'No packed packages match the filters.' : 'No packed packages yet.'}
                />
              </div>
            </section>

            <section className="ck-zone ck-zone--schedule" aria-labelledby="ck-zone-pickups">
              <h2 id="ck-zone-pickups" className="ck-zone__title">Pickups by day</h2>
              <div className="ck-days" tabIndex={0} aria-label="Pickup days, scrolls horizontally">
                {schedule.map(day => <DayColumn key={day.key} day={day} />)}
              </div>
            </section>
          </div>
        )}

        <p className="sr-only" aria-live="polite" aria-atomic="true">{announcement}</p>
        {undo && <UndoToast tracking={packageTitle(undo)} onUndo={undoPickup} onDismiss={() => setUndo(null)} />}
      </div>
    </ChecklistContext.Provider>
  )
}
