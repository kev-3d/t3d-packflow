import type { Client, Product } from './types'

export interface ManualItem {
  description: string
  quantity: number
  unit_price: number
}

/** Case-, accent- and spacing-insensitive form used to compare names. */
export const normalizeName = (value: string) =>
  value.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/\s+/g, ' ').trim()

/** Records whose name contains the query; names starting with it come first. An empty query lists them by name. */
export function matchByName<T>(records: T[], nameOf: (record: T) => string, query: string, limit = 8): T[] {
  const needle = normalizeName(query)
  const byName = (a: T, b: T) => nameOf(a).localeCompare(nameOf(b))
  if (!needle) return [...records].sort(byName).slice(0, limit)
  const scored = records
    .map(record => ({ record, at: normalizeName(nameOf(record)).indexOf(needle) }))
    .filter(entry => entry.at >= 0)
  scored.sort((a, b) => Number(a.at !== 0) - Number(b.at !== 0) || byName(a.record, b.record))
  return scored.slice(0, limit).map(entry => entry.record)
}

/** The record whose name equals the text once normalized, if any. */
export function findByName<T>(records: T[], nameOf: (record: T) => string, text: string): T | undefined {
  const needle = normalizeName(text)
  if (!needle) return undefined
  return records.find(record => normalizeName(nameOf(record)) === needle)
}

const productName = (product: Product) => product.name
const activeProducts = (products: Product[]) => products.filter(product => product.active !== 0)

/** Active catalog products whose name contains the query; names starting with it come first. */
export const matchProducts = (products: Product[], query: string, limit = 8) => matchByName(activeProducts(products), productName, query, limit)

/** The active catalog product whose name matches the description exactly (normalized), if any. */
export const findCatalogProduct = (products: Product[], description: string) => findByName(activeProducts(products), productName, description)

/** Fill a manual line from a catalog product: exact catalog name and catalog price; quantity is kept. */
export function applyCatalogProduct(item: ManualItem, product: Product): ManualItem {
  return { ...item, description: product.name, unit_price: product.price_eur || 0 }
}

const clientName = (client: Client) => client.customer

/** Saved clients whose name contains the query; names starting with it come first. */
export const matchClients = (clients: Client[], query: string, limit = 8) => matchByName(clients, clientName, query, limit)

/**
 * The saved client this name links to. Uses the backend's own rule, lower(trim(customer)), so "Known client"
 * is only shown when the order will really be linked (accents and inner spacing must match).
 */
export function findClient(clients: Client[], name: string): Client | undefined {
  const needle = name.trim().toLowerCase()
  if (!needle) return undefined
  return clients.find(client => client.customer.trim().toLowerCase() === needle)
}

/** Delivery address to suggest for a client: its shipping address, else its main address. Never replaces typed text. */
export function suggestedDeliveryAddress(current: string, client: Client): string {
  if (current.trim()) return current
  return client.shipping_address?.trim() || client.address?.trim() || ''
}
