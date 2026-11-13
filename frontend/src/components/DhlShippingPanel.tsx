import { AlertTriangle, Check, LoaderCircle, Truck } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { Input } from './ui/input'
import { canAutoQuote, demandFor, formatMoney, fuelFor, initialInputs, numberOrNull, quotePayload, todayIso } from '../dhlShipping'
import type { DetectedDestination, DhlConfig, SavedShippingQuote, ShippingQuoteInputs } from '../types'

interface Props {
  orderId: number
  planId: number | null
  orderCurrency: string
}

const SOURCE_LABELS = { dhl: 'DHL published rate (auto)', manual: 'Manual override' } as const
const AUTO_DELAY_MS = 450

export function DhlShippingPanel({ orderId, planId, orderCurrency }: Props) {
  const [config, setConfig] = useState<DhlConfig | null>(null)
  const [quote, setQuote] = useState<SavedShippingQuote | null>(null)
  const [stale, setStale] = useState(false)
  const [detected, setDetected] = useState<DetectedDestination | null>(null)
  const [inputs, setInputs] = useState<ShippingQuoteInputs | null>(null)
  const [loadError, setLoadError] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const lastSent = useRef('')

  useEffect(() => {
    let active = true
    Promise.all([api.dhlConfig(), api.shippingQuote(orderId)])
      .then(([cfg, saved]) => {
        if (!active) return
        setConfig(cfg)
        setQuote(saved.quote)
        setStale(saved.stale)
        setDetected(saved.detected_destination)
        const initial = initialInputs(cfg, saved.quote, saved.detected_destination?.code ?? null, todayIso())
        // A fresh saved quote needs no recalculation; anything else is priced automatically.
        lastSent.current = saved.quote && !saved.stale ? JSON.stringify(quotePayload(initial)) : ''
        setInputs(initial)
      })
      .catch(err => active && setLoadError(err instanceof Error ? err.message : 'DHL rates could not be loaded.'))
    return () => { active = false }
  }, [orderId, planId])

  // Price automatically whenever the inputs are complete and differ from the last saved quote.
  useEffect(() => {
    if (!config || !inputs || !canAutoQuote(inputs, config)) return
    const payload = quotePayload(inputs)
    const key = JSON.stringify(payload)
    if (key === lastSent.current) return
    const timer = window.setTimeout(async () => {
      lastSent.current = key
      setBusy(true)
      try {
        const response = await api.saveShippingQuote(orderId, payload)
        setQuote(response.quote); setStale(false); setError('')
      } catch (err) {
        setError(err instanceof Error ? err.message : 'The DHL quote could not be calculated.')
      } finally { setBusy(false) }
    }, AUTO_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [config, inputs, orderId])

  const destination = useMemo(
    () => config?.destinations.find(d => d.code === inputs?.destination),
    [config, inputs?.destination],
  )

  if (loadError) return <section className="panel dhl-panel"><p className="dhl-panel__error" role="alert">{loadError}</p></section>
  if (!config || !inputs) return <section className="panel dhl-panel"><p className="dhl-panel__loading"><LoaderCircle className="spin" size={16} /> Loading DHL rates…</p></section>

  const fuelWeek = fuelFor(config, inputs.shipment_date)
  const demand = demandFor(config, inputs.destination, inputs.shipment_date)
  const weeks = config.surcharges.fuel.weeks
  const set = (patch: Partial<ShippingQuoteInputs>) => { setInputs({ ...inputs, ...patch }); setError('') }
  const fuelValue = inputs.fuel_source === 'manual' ? inputs.fuel_pct : fuelWeek?.pct
  const demandValue = inputs.demand_source === 'manual' ? inputs.demand_per_kg : (inputs.destination ? demand.perKg : null)
  const result = quote?.result
  const incomplete = !canAutoQuote(inputs, config)

  return (
    <details className="panel dhl-panel" open>
      <summary className="dhl-panel__summary">
        <span><Truck size={17} /> DHL shipping</span>
        <small>{config.meta.version.replace('DHL-MT-', '')} CONTRACT · {config.meta.currency}</small>
      </summary>

      <div className="dhl-panel__body">
        <div className="dhl-grid">
          <label><span>Route</span><select value="export" disabled><option value="export">Export from Malta</option></select></label>
          <label><span>Destination country / area</span>
            <select value={inputs.destination} onChange={e => set({ destination: e.target.value })}>
              <option value="">Select a destination</option>
              {config.destinations.map(d => <option key={d.code} value={d.code}>{d.name} · Zone {d.zone}</option>)}
            </select>
            <small className={detected ? '' : 'dhl-warning'}>
              {detected
                ? inputs.destination === detected.code ? `Detected from ${detected.source}.` : `Detected ${detected.name} from ${detected.source}; changed manually.`
                : 'No destination country found in the PO. Select it manually.'}
            </small>
          </label>
          <label><span>Shipment date</span><Input type="date" max={config.meta.valid_to} value={inputs.shipment_date} onChange={e => set({ shipment_date: e.target.value })} /></label>
          <label><span>Delivery service</span>
            <select value={inputs.service} onChange={e => set({ service: e.target.value as ShippingQuoteInputs['service'] })}>
              {config.services.map(s => <option key={s.id} value={s.id}>{s.label}{s.premium_eur ? ` (+${s.premium_eur.toFixed(2)} EUR)` : ''}</option>)}
            </select>
          </label>
        </div>
        <p className="dhl-note">
          One multi-piece shipment, not a separate minimum charge per box.{destination ? ` Selected tariff zone ${destination.zone}.` : ''} Express Worldwide export rates from the DHL 2026 rate card. Third-country and import shipments are not supported. Service availability must be confirmed with DHL.
        </p>

        <h3 className="dhl-subtitle">Shipping surcharges &amp; VAT</h3>
        <div className="dhl-grid">
          <label><span>GoGreen Plus (€/kg)</span><Input type="number" min="0" step="0.01" value={inputs.gogreen_per_kg ?? ''} onChange={e => set({ gogreen_per_kg: numberOrNull(e.target.value) })} /></label>
          <label><span>Fuel surcharge (%)</span><Input type="number" min="0" step="0.01" readOnly={inputs.fuel_source !== 'manual'} className={inputs.fuel_source !== 'manual' ? 'dhl-readonly' : ''} value={fuelValue ?? ''} onChange={e => set({ fuel_pct: numberOrNull(e.target.value) })} /></label>
          <label><span>Demand surcharge (€/kg)</span><Input type="number" min="0" step="0.01" readOnly={inputs.demand_source !== 'manual'} className={inputs.demand_source !== 'manual' ? 'dhl-readonly' : ''} value={demandValue ?? ''} onChange={e => set({ demand_per_kg: numberOrNull(e.target.value) })} /></label>
          <label><span>VAT (%)</span><Input type="number" min="0" max="100" step="0.01" value={inputs.vat_pct ?? ''} onChange={e => set({ vat_pct: numberOrNull(e.target.value) })} /></label>
        </div>
        <div className="dhl-grid dhl-grid--2">
          <label><span>Fuel rate source</span>
            <select value={inputs.fuel_source} onChange={e => set({ fuel_source: e.target.value as 'dhl' | 'manual', fuel_pct: inputs.fuel_pct ?? fuelWeek?.pct ?? null })}>
              {(['dhl', 'manual'] as const).map(s => <option key={s} value={s}>{SOURCE_LABELS[s]}</option>)}
            </select>
            <small className={inputs.fuel_source === 'dhl' && !fuelWeek ? 'dhl-warning' : ''}>
              {fuelWeek
                ? `DHL International fuel, week ${fuelWeek.from} – ${fuelWeek.to}: ${fuelWeek.pct.toFixed(2)}%.`
                : `DHL has not published a fuel rate for this date in the imported schedule (${weeks[0]?.from} – ${weeks[weeks.length - 1]?.to}).`}
            </small>
          </label>
          <label><span>Demand rate source</span>
            <select value={inputs.demand_source} onChange={e => set({ demand_source: e.target.value as 'dhl' | 'manual', demand_per_kg: inputs.demand_per_kg ?? demand.perKg })}>
              {(['dhl', 'manual'] as const).map(s => <option key={s} value={s}>{SOURCE_LABELS[s]}</option>)}
            </select>
            <small>
              {inputs.destination
                ? demand.active
                  ? `Europe → ${demand.label}: ${demand.perKg.toFixed(2)} €/kg (DHL demand period ${config.surcharges.demand.valid_from} – ${config.surcharges.demand.valid_to}).`
                  : `Outside the DHL demand period (${config.surcharges.demand.valid_from} – ${config.surcharges.demand.valid_to}): no demand surcharge.`
                : 'Select a destination to look up its DHL demand region.'}
            </small>
          </label>
        </div>
        <div className="dhl-options">
          <label className="dhl-check"><input type="checkbox" checked={inputs.remote_area} onChange={e => set({ remote_area: e.target.checked })} /> Remote area delivery (0.50 €/kg, min. 24.00 €)</label>
          <label className="dhl-inline"><span>Other charges (€)</span><Input type="number" min="0" step="0.01" value={inputs.other_eur} onChange={e => set({ other_eur: numberOrNull(e.target.value) ?? 0 })} /></label>
        </div>
        <p className="dhl-note">
          Fuel applies to transport, premium, handling, remote area and demand charges, not GoGreen. VAT applies to the complete shipping subtotal. Per-kg surcharges use the rounded shipment tariff weight. The quote is recalculated and saved automatically when any value changes.
        </p>

        <div className="dhl-status" aria-live="polite">
          {busy ? <span><LoaderCircle className="spin" size={15} /> Calculating DHL quote…</span>
            : error ? <span className="dhl-panel__error" role="alert"><AlertTriangle size={15} /> {error}</span>
            : incomplete ? <span className="dhl-warning"><AlertTriangle size={15} /> {!inputs.destination ? 'Select a destination to calculate the DHL quote.' : 'A fuel or demand rate is missing for this date. Use a manual override to calculate.'}</span>
            : result ? <span className="dhl-ok"><Check size={15} /> Quote up to date and saved with the order.</span> : null}
        </div>

        {result && (
          <div className={`dhl-result${busy || error || stale ? ' dhl-result--dim' : ''}`}>
            {stale && !busy && <p className="dhl-warning" role="status"><AlertTriangle size={15} /> The packing plan changed after this quote was saved.</p>}
            <div className="dhl-result__head">
              <div><span>Estimated DHL cost</span><strong>{formatMoney(result.total, result.currency)}</strong></div>
              <p>{result.service_label} · {result.destination.name} (zone {result.destination.zone}) · {result.shipment_date} · tariff weight {result.tariff_weight_kg} kg{orderCurrency !== result.currency ? ` · calculated in ${result.currency}, order is in ${orderCurrency}` : ''}</p>
            </div>
            <table className="dhl-table">
              <thead><tr><th>Box</th><th>Dimensions (cm)</th><th>Actual kg</th><th>Volumetric kg</th><th>Chargeable kg</th><th>Handling</th></tr></thead>
              <tbody>
                {result.pieces.map(p => <tr key={p.sequence}><td>{p.box_code || p.box_name || `Box ${p.sequence}`}</td><td>{p.dimensions_cm.join(' × ')}</td><td>{p.actual_kg.toFixed(2)}</td><td>{p.volumetric_kg.toFixed(2)}</td><td>{p.chargeable_kg.toFixed(2)}</td><td>{p.handling.amount ? `${p.handling.label} · ${p.handling.amount.toFixed(2)}` : '—'}</td></tr>)}
              </tbody>
            </table>
            <dl className="dhl-breakdown">
              {result.lines.map(l => <div key={l.key}><dt>{l.label}</dt><dd>{formatMoney(l.amount, result.currency)}</dd></div>)}
              <div className="dhl-breakdown__sub"><dt>Subtotal</dt><dd>{formatMoney(result.subtotal, result.currency)}</dd></div>
              <div><dt>VAT · {result.rates.vat_pct}%</dt><dd>{formatMoney(result.vat, result.currency)}</dd></div>
              <div className="dhl-breakdown__total"><dt>Total</dt><dd>{formatMoney(result.total, result.currency)}</dd></div>
            </dl>
            <p className="dhl-note">
              Static snapshot {result.tariff_version}; fuel and demand from the DHL schedules imported {config.surcharges.fuel.imported_on ?? '—'} / {config.surcharges.demand.imported_on ?? '—'}. No online lookup runs during calculation. Saved {quote?.created_at.replace('T', ' ').slice(0, 16)} UTC.
            </p>
          </div>
        )}
      </div>
    </details>
  )
}
