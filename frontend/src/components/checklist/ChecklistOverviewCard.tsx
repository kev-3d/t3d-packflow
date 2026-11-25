import { AlertCircle, ArrowRight, CheckCircle2, ClipboardCheck, LoaderCircle } from 'lucide-react'
import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { api } from '../../api'
import { useToday } from '../../checklist/hooks'
import {
  carrierLabel, countryName, packageFromApi, packageTitle, selectDayTabs, urgencyOf,
  type DayTab, type Package,
} from '../../checklist/model'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card'
import { CarrierBadge } from './PackageCard'
import '../../checklist.css'

const REFRESH_MS = 60_000

type Props = {
  /** Opens the full Checklist page. */
  onOpenChecklist: () => void
}

/** Overview card: the shipment checklist one day at a time (To pack, Packed, Pickups). */
export function ChecklistOverviewCard({ onOpenChecklist }: Props) {
  const today = useToday()
  const [packages, setPackages] = useState<Package[] | null>(null)
  const [failed, setFailed] = useState(false)

  const load = useCallback(async () => {
    try {
      setPackages((await api.checklist()).map(packageFromApi))
      setFailed(false)
    } catch {
      setFailed(true)
    }
  }, [])

  useEffect(() => {
    load()
    const timer = setInterval(() => { if (document.visibilityState === 'visible') load() }, REFRESH_MS)
    return () => clearInterval(timer)
  }, [load])

  const tabs = useMemo(() => (packages ? selectDayTabs(packages, today) : []), [packages, today])
  const [selected, setSelected] = useState<string>(today)
  const current = tabs.find(tab => tab.key === selected) ?? tabs.find(tab => tab.key === today) ?? tabs[0]

  return (
    <Card className="shipments-card">
      <div className="ck-ov">
        <CardHeader className="ck-ov__head">
          <CardTitle className="flex items-center gap-2"><ClipboardCheck size={19} aria-hidden="true" /> Shipment checklist</CardTitle>
          <button type="button" className="ck-ov__open" onClick={onOpenChecklist}>Open<ArrowRight size={15} aria-hidden="true" /></button>
        </CardHeader>
        <CardContent className="px-5">
          {!packages && !failed && <div className="ck-ov__state" role="status"><LoaderCircle className="spin" size={20} aria-hidden="true" /><p>Loading shipments…</p></div>}
          {!packages && failed && (
            <div className="ck-ov__state" role="alert">
              <AlertCircle size={20} aria-hidden="true" /><p>The checklist could not be loaded.</p>
              <button type="button" className="ck-btn ck-btn--ghost" onClick={load}>Try again</button>
            </div>
          )}
          {packages && current && <DayTabs tabs={tabs} current={current} today={today} onSelect={setSelected} onOpenChecklist={onOpenChecklist} />}
        </CardContent>
      </div>
    </Card>
  )
}

type DayTabsProps = { tabs: DayTab[]; current: DayTab; today: string; onSelect: (key: string) => void; onOpenChecklist: () => void }

function DayTabs({ tabs, current, today, onSelect, onOpenChecklist }: DayTabsProps) {
  const baseId = useId()
  const tabRefs = useRef(new Map<string, HTMLButtonElement>())

  // Arrow keys move between tabs (roving focus), as in a native tab list.
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = tabs.findIndex(tab => tab.key === current.key)
    const next = event.key === 'ArrowRight' ? index + 1 : event.key === 'ArrowLeft' ? index - 1
      : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null
    if (next === null) return
    event.preventDefault()
    const tab = tabs[(next + tabs.length) % tabs.length]
    onSelect(tab.key)
    tabRefs.current.get(tab.key)?.focus()
  }

  const total = (tab: DayTab) => tab.toPack.length + tab.packed.length
  return (
    <>
      <div className="ck-ov__tabs" role="tablist" aria-label="Pickup day" onKeyDown={onKeyDown}>
        {tabs.map(tab => {
          const selected = tab.key === current.key
          return (
            <button
              key={tab.key}
              ref={element => { if (element) tabRefs.current.set(tab.key, element); else tabRefs.current.delete(tab.key) }}
              type="button"
              role="tab"
              id={`${baseId}-tab-${tab.key}`}
              aria-selected={selected}
              aria-controls={`${baseId}-panel`}
              tabIndex={selected ? 0 : -1}
              data-kind={tab.kind}
              className="ck-ov__tab"
              onClick={() => onSelect(tab.key)}
              aria-label={`${tab.title}, ${total(tab)} ${total(tab) === 1 ? 'package' : 'packages'}`}
            >
              <b>{tab.title}</b>
              <small>{tab.kind === 'day' ? `${tab.subtitle} · ${total(tab)}` : total(tab)}</small>
            </button>
          )
        })}
      </div>

      <div className="ck-ov__panel" role="tabpanel" id={`${baseId}-panel`} aria-labelledby={`${baseId}-tab-${current.key}`} tabIndex={0}>
        <PackageSection kind="to_pack" title="To pack" packages={current.toPack} empty="Nothing left to pack." today={today} onOpenChecklist={onOpenChecklist} />
        <PackageSection kind="packed" title="Packed" packages={current.packed} empty="Nothing packed yet." today={today} onOpenChecklist={onOpenChecklist} />
        {current.kind !== 'unscheduled' && (
          <section className="ck-ov__section" data-kind="pickups">
            <h3>Pickups <span className="ck-count ck-count--sm" aria-label={`${total(current)} to collect`}>{total(current)}</span></h3>
            {current.pickups.length ? (
              <ul>
                {current.pickups.map(group => (
                  <li key={group.key} className="ck-ov__pickup">
                    <CarrierBadge carrier={group.carrier} />
                    <span>· {group.slot ?? 'No time slot'}</span>
                    <span className="ck-count ck-count--sm" aria-label={`${group.packages.length} ${group.packages.length === 1 ? 'package' : 'packages'}`}>{group.packages.length}</span>
                  </li>
                ))}
              </ul>
            ) : <p className="ck-ov__none">No pickups to collect.</p>}
            {current.pickedUp > 0 && <p className="ck-ov__done"><CheckCircle2 size={14} aria-hidden="true" />{current.pickedUp} already picked up</p>}
          </section>
        )}
      </div>
    </>
  )
}

type SectionProps = { kind: 'to_pack' | 'packed'; title: string; packages: Package[]; empty: string; today: string; onOpenChecklist: () => void }

function PackageSection({ kind, title, packages, empty, today, onOpenChecklist }: SectionProps) {
  return (
    <section className="ck-ov__section" data-kind={kind}>
      <h3>{title} <span className="ck-count ck-count--sm" aria-label={`${packages.length} ${packages.length === 1 ? 'package' : 'packages'}`}>{packages.length}</span></h3>
      {packages.length ? (
        <ul>
          {packages.map(pkg => {
            const urgency = urgencyOf(pkg, today)
            return (
              <li key={pkg.id}>
                <button type="button" className="ck-ov__row" data-urgency={urgency ?? undefined} onClick={onOpenChecklist}
                  title={`Open ${packageTitle(pkg)} in the checklist`}>
                  <span>
                    <b>{packageTitle(pkg)}{urgency ? ` · ${urgency === 'late' ? 'Overdue' : 'Urgent'}` : ''}</b>
                    <small>{pkg.customer} · {countryName(pkg.destination)}</small>
                  </span>
                  <span className="ck-carrier" data-carrier={pkg.carrier || 'none'}>{carrierLabel(pkg.carrier)}</span>
                </button>
              </li>
            )
          })}
        </ul>
      ) : <p className="ck-ov__none">{empty}</p>}
    </section>
  )
}
