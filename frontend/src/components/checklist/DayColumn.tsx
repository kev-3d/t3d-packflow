import { AlertTriangle, CalendarCheck, ChevronDown, PackageOpen } from 'lucide-react'
import { useId, useState } from 'react'
import type { CarrierGroupView, DayView } from '../../checklist/model'
import { useChecklist } from './context'
import { EmptyState, useDropTarget } from './KanbanColumn'
import { CarrierBadge, PackageCard } from './PackageCard'

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

export function CarrierGroup({ group }: { group: CarrierGroupView }) {
  const [open, setOpen] = useState(true)
  const listId = useId()
  const count = group.packages.length
  const slot = group.slot ?? 'No time slot'
  return (
    <section className="ck-group" data-open={open || undefined}>
      <h4 className="ck-group__title">
        <button
          type="button"
          className="ck-group__toggle"
          aria-expanded={open}
          aria-controls={listId}
          aria-label={`${group.carrier} · ${slot}: ${plural(count, 'package', 'packages')}`}
          onClick={() => setOpen(value => !value)}
        >
          <ChevronDown aria-hidden="true" size={16} className="ck-group__chevron" />
          <CarrierBadge carrier={group.carrier} />
          <span className="ck-group__slot">· {slot}</span>
          <span className="ck-count ck-count--sm" aria-hidden="true">{count}</span>
        </button>
      </h4>
      <ul id={listId} className="ck-list" hidden={!open}>
        {group.packages.map(pkg => <li key={pkg.id}><PackageCard pkg={pkg} placement="schedule" /></li>)}
      </ul>
    </section>
  )
}

export function DayColumn({ day }: { day: DayView }) {
  const { actions, today } = useChecklist()
  const headingId = useId()
  const drop = useDropTarget(
    status => day.kind === 'day' && status === 'empacado',
    pkg => { if (day.date) actions.assignPickup(pkg, day.date, pkg.pickupSlot ?? 'Morning') },
  )
  const empty = day.kind === 'late' ? 'Nothing overdue with these filters.' : 'No pickups scheduled.'
  return (
    <section className="ck-day" data-kind={day.kind} data-today={day.date === today || undefined} aria-labelledby={headingId}>
      <header className="ck-day__head">
        <h3 id={headingId}>
          {day.kind === 'late' && <AlertTriangle aria-hidden="true" size={15} />}
          {day.title} <small>{day.subtitle}</small>
        </h3>
        <p className="ck-day__meta">
          <span>{plural(day.total, 'package', 'packages')}</span>
          {day.unpacked > 0 && <span className="ck-day__warn"><PackageOpen aria-hidden="true" size={14} />{day.unpacked} not packed</span>}
        </p>
      </header>
      <div className="ck-day__body" data-focus-scope tabIndex={-1} data-drop-over={drop.over || undefined} {...drop.handlers}>
        {day.groups.length
          ? day.groups.map(group => <CarrierGroup key={group.key} group={group} />)
          : <EmptyState icon={<CalendarCheck aria-hidden="true" size={20} />}>{empty}</EmptyState>}
      </div>
    </section>
  )
}
