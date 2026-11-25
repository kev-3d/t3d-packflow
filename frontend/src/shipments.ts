import type { ShipmentDocField, ShipmentEmailField } from './types'

export const FORWARDERS = ['DHL', 'DHL insured', 'TNT', 'UPS', 'MP', 'Shiplowcost', 'Other'] as const

/** Document checks in the order of the Export sheet. */
export const DOC_COLUMNS: { field: ShipmentDocField; label: string; title: string }[] = [
  { field: 'doc_pl', label: 'PL', title: 'Packing list' },
  { field: 'doc_sds', label: 'SDS', title: 'Safety data sheet' },
  { field: 'doc_stripe', label: 'Stripe', title: 'Stripe payment' },
  { field: 'doc_tsca', label: 'TSCA', title: 'TSCA certification (US only)' },
  { field: 'doc_sec232', label: 'Sec. 232', title: 'Section 232 declaration in invoice (US only)' },
  { field: 'doc_coo', label: 'CoO', title: 'Certificate of origin' },
]

export const EMAIL_COLUMNS: { field: ShipmentEmailField; label: string; title: string }[] = [
  { field: 'email_client_sent', label: 'Email client', title: 'Email sent to client (after collection)' },
  { field: 'email_forwarder_sent', label: 'Email courier', title: 'Email sent to the courier (after booking: tracking + invoice)' },
]

/** Public tracking page of a forwarder, or null when there is no tracking number. */
export function trackingUrl(forwarder: string, tracking: string): string | null {
  const number = tracking.trim()
  if (!number) return null
  const id = encodeURIComponent(number)
  switch (forwarder) {
    case 'DHL':
    case 'DHL insured':
      return `https://www.dhl.com/global-en/home/tracking/tracking-express.html?submit=1&tracking-id=${id}`
    case 'TNT':
      return `https://www.tnt.com/express/en_gc/site/shipping-tools/tracking.html?searchType=con&cons=${id}`
    case 'UPS':
      return `https://www.ups.com/track?tracknum=${id}`
    default:
      return `https://t.17track.net/en#nums=${id}`
  }
}

export const todayIso = () => {
  const now = new Date()
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000).toISOString().slice(0, 10)
}

/** 2026-11-24 → 24.11.26, as the dates are written in the Export sheet. */
export function sheetDate(iso: string): string {
  const [year, month, day] = iso.split('-')
  return year && month && day ? `${day}.${month}.${year.slice(2)}` : iso
}
