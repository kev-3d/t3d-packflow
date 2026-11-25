import { CheckCircle2, FilterX, PackageOpen, RefreshCw, Search, Siren, Truck, X } from 'lucide-react'
import type { RefObject } from 'react'
import { carrierLabel, type Carrier, type Summary } from '../../checklist/model'

type SummaryBadgesProps = { summary: Summary }

export function SummaryBadges({ summary }: SummaryBadgesProps) {
  const items = [
    { key: 'pending', label: 'Pending to pack', value: summary.pendingPacking, icon: PackageOpen, tone: 'amber' },
    { key: 'today', label: 'Pickups today', value: summary.pickupsToday, icon: Truck, tone: 'blue' },
    { key: 'done', label: 'Picked up today', value: summary.pickedUpToday, icon: CheckCircle2, tone: 'green' },
  ] as const
  return (
    <ul className="ck-summary" aria-label="Today's summary" aria-live="polite">
      {items.map(({ key, label, value, icon: Icon, tone }) => (
        <li key={key} className="ck-summary__item" data-tone={tone}>
          <Icon aria-hidden="true" size={16} />
          <span className="ck-summary__label">{label}</span>
          <strong className="ck-summary__value">{value}</strong>
        </li>
      ))}
    </ul>
  )
}

type HeaderProps = {
  dateLabel: string
  summary: Summary
  /** When the board last loaded from the server (null until the first load). */
  updatedAt: Date | null
  refreshing: boolean
  onRefresh: () => void
}

const timeFormat = new Intl.DateTimeFormat('en-US', { hour: '2-digit', minute: '2-digit' })

export function Header({ dateLabel, summary, updatedAt, refreshing, onRefresh }: HeaderProps) {
  return (
    <header className="ck-header">
      <div className="ck-header__title">
        <h1>Shipment checklist</h1>
        <p>
          {dateLabel}
          {updatedAt && <span className="ck-updated"> · Updated {timeFormat.format(updatedAt)}</span>}
        </p>
      </div>
      <div className="ck-header__side">
        <SummaryBadges summary={summary} />
        <button type="button" className="ck-btn ck-btn--ghost ck-btn--icon" onClick={onRefresh} disabled={refreshing} aria-label="Refresh board" title="Refresh board">
          <RefreshCw aria-hidden="true" size={16} className={refreshing ? 'spin' : undefined} />
        </button>
      </div>
    </header>
  )
}

type FiltersProps = {
  searchRef: RefObject<HTMLInputElement | null>
  query: string
  onQueryChange: (value: string) => void
  /** Carriers on the board right now. */
  availableCarriers: Carrier[]
  carriers: Carrier[]
  onToggleCarrier: (carrier: Carrier) => void
  onlyUrgent: boolean
  onToggleUrgent: () => void
  onClear: () => void
  active: boolean
  visible: number
  total: number
}

export function Filters(props: FiltersProps) {
  const { searchRef, query, onQueryChange, availableCarriers, carriers, onToggleCarrier, onlyUrgent, onToggleUrgent, onClear, active, visible, total } = props
  return (
    <div className="ck-filters" role="search" aria-label="Search and filter packages">
      <div className="ck-search">
        <Search aria-hidden="true" size={16} />
        <input
          ref={searchRef}
          type="search"
          aria-label="Search by tracking number, PO or customer"
          aria-keyshortcuts="/"
          placeholder="Search tracking, PO or customer"
          value={query}
          onChange={event => onQueryChange(event.target.value)}
          onKeyDown={event => { if (event.key === 'Escape' && query) { event.preventDefault(); onQueryChange('') } }}
        />
        {query
          ? <button type="button" className="ck-search__clear" aria-label="Clear search" onClick={() => { onQueryChange(''); searchRef.current?.focus() }}><X aria-hidden="true" size={16} /></button>
          : <kbd aria-hidden="true">/</kbd>}
      </div>

      <div className="ck-chips" role="group" aria-label="Filter by carrier">
        {[...new Set([...availableCarriers, ...carriers])].map(carrier => {
          const pressed = carriers.includes(carrier)
          return (
            <button key={carrier} type="button" className="ck-chip" data-carrier={carrier || 'none'} aria-pressed={pressed} onClick={() => onToggleCarrier(carrier)}>
              <i aria-hidden="true" />{carrierLabel(carrier)}
            </button>
          )
        })}
      </div>

      <button type="button" className="ck-chip ck-chip--urgent" aria-pressed={onlyUrgent} onClick={onToggleUrgent}>
        <Siren aria-hidden="true" size={15} />Urgent / overdue only
      </button>

      <button type="button" className="ck-btn ck-btn--ghost" onClick={onClear} disabled={!active}>
        <FilterX aria-hidden="true" size={16} />Clear filters
      </button>

      <p className="ck-results" aria-live="polite">
        {active ? <>Showing <b>{visible}</b> of {total} packages</> : <>{total} active packages</>}
      </p>
    </div>
  )
}
