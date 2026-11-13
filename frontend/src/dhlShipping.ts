import type { DhlConfig, DhlFuelWeek, SavedShippingQuote, ShippingQuoteInputs } from './types'

/** DHL's weekly fuel surcharge row covering the shipment date, if DHL has published it. */
export function fuelFor(config: DhlConfig, date: string): DhlFuelWeek | null {
  if (!date) return null
  return config.surcharges.fuel.weeks.find(week => week.from <= date && date <= week.to) ?? null
}

/** Demand surcharge for a destination on a date (0 outside DHL's demand period). */
export function demandFor(config: DhlConfig, code: string, date: string) {
  const { demand, region_labels } = config.surcharges
  const region = config.destinations.find(d => d.code === code)?.demand_region ?? 'rest_of_world'
  const active = Boolean(date) && demand.valid_from <= date && date <= demand.valid_to
  return { perKg: active ? demand.rates[region] : 0, region, label: region_labels[region], active }
}

export function todayIso(now = new Date()): string {
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000)
  return local.toISOString().slice(0, 10)
}

/** Form values: the saved quote's inputs, else today's date and the destination detected from the PO. */
export function initialInputs(config: DhlConfig, saved: SavedShippingQuote | null, detected: string | null, today: string): ShippingQuoteInputs {
  if (saved) {
    const inputs = { ...saved.inputs }
    if (inputs.fuel_source === 'saved') inputs.fuel_source = 'dhl'
    if (inputs.demand_source === 'saved') inputs.demand_source = 'dhl'
    return inputs
  }
  return {
    destination: detected || '',
    shipment_date: today > config.meta.valid_to ? config.meta.valid_to : today,
    service: 'express_worldwide',
    fuel_source: 'dhl',
    fuel_pct: null,
    demand_source: 'dhl',
    demand_per_kg: null,
    gogreen_per_kg: config.surcharges.defaults.gogreen_per_kg,
    vat_pct: config.surcharges.defaults.vat_pct,
    remote_area: false,
    other_eur: 0,
  }
}

/** What is sent: DHL-schedule values are resolved on the server, so they are dropped here. */
export function quotePayload(inputs: ShippingQuoteInputs): ShippingQuoteInputs {
  return {
    ...inputs,
    fuel_pct: inputs.fuel_source === 'manual' ? inputs.fuel_pct : null,
    demand_per_kg: inputs.demand_source === 'manual' ? inputs.demand_per_kg : null,
  }
}

/** Inputs complete enough to price without the user (manual sources need their values). */
export function canAutoQuote(inputs: ShippingQuoteInputs, config: DhlConfig): boolean {
  if (!inputs.destination || !inputs.shipment_date) return false
  if (inputs.fuel_source === 'manual' ? inputs.fuel_pct === null : !fuelFor(config, inputs.shipment_date)) return false
  if (inputs.demand_source === 'manual' && inputs.demand_per_kg === null) return false
  return inputs.vat_pct !== null && inputs.gogreen_per_kg !== null
}

export function formatMoney(value: number, currency = 'EUR'): string {
  return `${currency} ${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export function numberOrNull(value: string): number | null {
  if (value.trim() === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}
