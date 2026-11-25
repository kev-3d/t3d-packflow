import { AlertTriangle, CalendarDays, Clock, PackageCheck, PackageOpen, Truck } from 'lucide-react'
import { useId, type MouseEvent } from 'react'
import {
  boxLabel, carrierLabel, compactDayLabel, countryName, packageTitle, parsePickupValue, pickupValue, splitMatches, urgencyOf,
  type Carrier, type Package, type Urgency,
} from '../../checklist/model'
import { keepFocusInScope, setDragPackage, useChecklist } from './context'

/** Where the card is rendered decides its single action. */
export type CardPlacement = 'por_empacar' | 'empacados' | 'schedule'

export function Highlight({ text }: { text: string }) {
  const { query } = useChecklist()
  return <>{splitMatches(text, query).map((part, i) => (part.match ? <mark key={i}>{part.text}</mark> : part.text))}</>
}

export function CarrierBadge({ carrier }: { carrier: Carrier }) {
  return <span className="ck-carrier" data-carrier={carrier || 'none'}>{carrierLabel(carrier)}</span>
}

export function UrgencyTag({ urgency }: { urgency: Exclude<Urgency, null> }) {
  return urgency === 'late'
    ? <span className="ck-tag ck-tag--late"><AlertTriangle aria-hidden="true" size={14} />Overdue</span>
    : <span className="ck-tag ck-tag--urgent"><Clock aria-hidden="true" size={14} />Urgent</span>
}

function PickupSelect({ pkg }: { pkg: Package }) {
  const { today, pickupOptions, actions } = useChecklist()
  const id = useId()
  const value = pickupValue(pkg)
  const listed = pickupOptions.some(option => option.value === value)
  const currentLabel = pkg.pickupDate
    ? `${pkg.pickupDate < today ? 'Overdue' : compactDayLabel(pkg.pickupDate, today)} · ${pkg.pickupSlot ?? 'No time slot'}`
    : ''
  return (
    <span className="ck-select" data-assigned={pkg.pickupDate ? true : undefined}>
      <CalendarDays aria-hidden="true" size={15} />
      <label htmlFor={id} className="sr-only">Pickup day for {packageTitle(pkg)}</label>
      <select
        id={id}
        data-card-action
        value={value}
        onChange={event => {
          const next = parsePickupValue(event.target.value)
          if (next) actions.assignPickup(pkg, next.date, next.slot)
        }}
      >
        {!pkg.pickupDate && <option value="" disabled>Assign pickup…</option>}
        {pkg.pickupDate && !listed && <option value={value} disabled>{currentLabel}</option>}
        {pickupOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </span>
  )
}

function CardAction({ pkg, placement }: { pkg: Package; placement: CardPlacement }) {
  const { actions } = useChecklist()
  const run = (action: () => void) => (event: MouseEvent<HTMLButtonElement>) => {
    keepFocusInScope(event.currentTarget)
    action()
  }

  if (placement === 'por_empacar') {
    return (
      <button type="button" className="ck-btn ck-btn--pack" data-card-action onClick={run(() => actions.markPacked(pkg))} aria-label={`Mark ${packageTitle(pkg)} as packed`}>
        <PackageCheck aria-hidden="true" size={16} />Mark packed
      </button>
    )
  }
  if (placement === 'empacados') return <PickupSelect pkg={pkg} />

  if (pkg.status !== 'empacado') {
    return (
      <button type="button" className="ck-btn ck-btn--blocked" disabled aria-label={`Confirm pickup of ${packageTitle(pkg)}: not packed yet`}>
        <PackageOpen aria-hidden="true" size={16} />Not packed yet
      </button>
    )
  }
  return (
    <button type="button" className="ck-btn ck-btn--pickup" data-card-action onClick={run(() => actions.confirmPickup(pkg))} aria-label={`Confirm pickup of ${packageTitle(pkg)}`}>
      <Truck aria-hidden="true" size={16} />Confirm pickup
    </button>
  )
}

export function PackageCard({ pkg, placement }: { pkg: Package; placement: CardPlacement }) {
  const { today } = useChecklist()
  const titleId = useId()
  const urgency = urgencyOf(pkg, today)
  const draggable = placement !== 'schedule'
  return (
    <article
      className="ck-card"
      data-urgency={urgency ?? undefined}
      aria-labelledby={titleId}
      draggable={draggable}
      onDragStart={draggable ? event => setDragPackage(event, pkg) : undefined}
    >
      <header className="ck-card__head">
        <h4 id={titleId} className="ck-card__track"><Highlight text={packageTitle(pkg)} /></h4>
        <CarrierBadge carrier={pkg.carrier} />
      </header>
      <p className="ck-card__sub"><Highlight text={pkg.customer} /> · {countryName(pkg.destination)}</p>
      <p className="ck-card__detail">
        {/* The PO is the title while there is no tracking number; don't repeat it. */}
        {pkg.tracking && pkg.po ? <><Highlight text={pkg.po} /> · </> : null}{boxLabel(pkg)}
      </p>
      <footer className="ck-card__foot">
        {urgency && <UrgencyTag urgency={urgency} />}
        <CardAction pkg={pkg} placement={placement} />
      </footer>
    </article>
  )
}
