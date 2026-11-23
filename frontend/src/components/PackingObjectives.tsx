import { Boxes, Check, Euro, LoaderCircle, Maximize2 } from 'lucide-react'
import type { PackingObjective, PackingOption } from '../types'

const ICONS: Record<PackingObjective, typeof Boxes> = {
  fewest_boxes: Boxes,
  lowest_dhl_cost: Euro,
  smallest_volume: Maximize2,
}

interface Props {
  options: PackingOption[]
  zone: number
  zoneAssumed: boolean
  busy: boolean
  pending?: PackingObjective
  onSelect: (objective: PackingObjective) => void
}

/** Compare the best plan for each packing objective and recalculate with another one. */
export function PackingObjectives({ options, zone, zoneAssumed, busy, pending, onSelect }: Props) {
  const best = {
    box_count: Math.min(...options.map(o => o.box_count)),
    estimated_dhl_eur: Math.min(...options.map(o => o.estimated_dhl_eur)),
    total_volume_l: Math.min(...options.map(o => o.total_volume_l)),
  }
  return (
    <section className="panel packing-objectives" aria-labelledby="packing-objectives-title">
      <header>
        <div>
          <h2 id="packing-objectives-title">Packing goal</h2>
          <p>Best plan found for each goal. DHL estimate: transport + piece surcharges for zone {zone}{zoneAssumed ? ' (destination not detected, assumed)' : ''}, before fuel, demand and VAT.</p>
        </div>
      </header>
      <div className="packing-objectives__grid" role="radiogroup" aria-label="Packing goal">
        {options.map(option => {
          const Icon = ICONS[option.objective]
          const isPending = pending === option.objective
          return (
            <button
              key={option.objective}
              type="button"
              role="radio"
              aria-checked={option.selected}
              className={`packing-objective${option.selected ? ' packing-objective--selected' : ''}`}
              disabled={busy || option.selected}
              onClick={() => onSelect(option.objective)}
            >
              <span className="packing-objective__title">
                {isPending ? <LoaderCircle className="spin" size={16} /> : option.selected ? <Check size={16} /> : <Icon size={16} />}
                {option.label}
              </span>
              <dl>
                <div className={option.box_count === best.box_count ? 'is-best' : ''}><dt>Boxes</dt><dd>{option.box_count}</dd></div>
                <div className={option.estimated_dhl_eur === best.estimated_dhl_eur ? 'is-best' : ''}><dt>DHL est.</dt><dd>€{option.estimated_dhl_eur.toFixed(2)}</dd></div>
                <div className={option.total_volume_l === best.total_volume_l ? 'is-best' : ''}><dt>Carton volume</dt><dd>{option.total_volume_l.toFixed(1)} L</dd></div>
                <div><dt>Utilization</dt><dd>{option.utilization}%</dd></div>
                <div><dt>Chargeable</dt><dd>{option.chargeable_kg.toFixed(2)} kg</dd></div>
              </dl>
              <small className="packing-objective__boxes">{option.boxes.map(b => `${b.count}× ${b.box_name}`).join(' · ')}</small>
              {!option.complete && <small className="form-error">Some units do not fit.</small>}
              <span className="packing-objective__action">{option.selected ? 'Current plan' : isPending ? 'Calculating…' : 'Use this plan'}</span>
            </button>
          )
        })}
      </div>
    </section>
  )
}
