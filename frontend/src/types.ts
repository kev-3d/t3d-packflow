export type Page = 'dashboard' | 'orders' | 'catalog' | 'clients' | 'order'

export interface OrderSummary {
  id: number
  order_number: string
  client: string
  vendor: string
  order_date: string
  requested_date?: string | null
  currency: string
  source_filename?: string
  source_type: 'pdf' | 'manual' | 'email'
  total_amount: number
  status: string
  confidence: number
  line_count: number
  unit_count: number
  box_count?: number
  average_utilization?: number
  total_weight_kg?: number
}

export interface DashboardData {
  kpis: {
    orders: number
    ready: number
    boxes: number
    average_utilization: number
    total_weight_kg: number
  }
  orders: OrderSummary[]
  utilization_series: { label: string; value: number }[]
}

export interface OrderItem {
  id?: number
  description: string
  quantity: number
  unit_price: number
  line_number?: number
  requested_date?: string | null
  tax_label?: string
  discount_percent?: number
  amount?: number
  is_suggested?: number
  product_id?: number | null
  product_name?: string
  /** SKU as written on the purchase order. */
  sku_raw?: string | null
  /** SKU of the matched catalog product. */
  sku?: string | null
  weight_kg?: number
  length_cm?: number
  width_cm?: number
  height_cm?: number
}

export interface Placement {
  uid: string
  sku: string
  name: string
  position: [number, number, number]
  dimensions: [number, number, number]
}

export interface PackedBox {
  sequence: number
  box_type_id: number
  box_code: string
  box_name: string
  dimensions_cm: [number, number, number]
  utilization: number
  product_weight_kg: number
  empty_weight_kg?: number
  gross_weight_kg: number
  max_weight_kg: number
  remaining_volume_l: number
  items: { sku: string; name: string; quantity: number }[]
  placements: Placement[]
}

export interface PackingResult {
  status: string
  box_count: number
  unit_count: number
  average_utilization: number
  total_weight_kg: number
  unplaced: { sku: string; name: string; uid: string }[]
  boxes: PackedBox[]
  suggestions: { type: string; title: string; detail: string }[]
  product_recommendations: {
    type: 'product'
    source_item_id: number
    product_id: number
    description: string
    quantity: number
    unit_price: number
    amount: number
    projected_utilization: number
    box_count: number
  }[]
  method: string
}

export interface PackingPlan {
  id: number
  order_id: number
  box_count: number
  average_utilization: number
  total_weight_kg: number
  created_at: string
  result: PackingResult
}

export interface OrderDetailData {
  id: number
  order_number: string
  client: string
  vendor: string
  order_date: string
  requested_date?: string | null
  currency: string
  source_filename?: string
  source_type: 'pdf' | 'manual' | 'email'
  delivery_address: string
  billing_address: string
  terms: string
  notes: string
  subtotal: number
  tax_amount: number
  total_amount: number
  status: string
  confidence: number
  items: OrderItem[]
  source_available?: boolean
  extraction_warnings?: string[]
  plan?: PackingPlan | null
}

export interface AnalysisReport {
  id: number
  order_id: number
  plan_id: number
  filename: string
  created_at: string
  download_url: string
}

export interface Product {
  id: number
  name: string
  length_cm: number
  width_cm: number
  height_cm: number
  weight_kg: number
  rotation_allowed: number
  fragile: number
  active: number
}

export interface BoxType {
  id: number
  code: string
  name: string
  length_cm: number
  width_cm: number
  height_cm: number
  empty_weight_kg: number
  max_weight_kg: number
  active: number
}


export interface Client {
  id: number
  customer: string
  phone_number: string
  email: string
  contact_name: string
  address: string
  country: string
  shipping_address: string
  /** ISO 3166-1 alpha-2 code resolved from `country` by the API; null when it is blank or unknown. */
  country_code?: string | null
  created_at: string
  updated_at: string
}

export interface ClientImportResult { imported: number; created: number; updated: number; errors: string[] }

export type ClientInput = Pick<Client, 'customer' | 'phone_number' | 'email' | 'contact_name' | 'address' | 'country' | 'shipping_address'>

export type DhlServiceId = 'express_worldwide' | 'premium_1200' | 'premium_0900'
/** 'dhl' = DHL's published weekly fuel / demand schedule. 'saved' is the legacy name of 'dhl'. */
export type DhlRateSource = 'dhl' | 'manual' | 'saved'
export type DemandRegion = 'china_hk' | 'asia' | 'oceania' | 'europe' | 'americas' | 'mena' | 'rest_of_world'

export interface DhlDestination { code: string; name: string; zone: number; demand_region?: DemandRegion }
export interface DetectedDestination extends DhlDestination { source: string }

export interface DhlFuelWeek { from: string; to: string; pct: number }

export interface DhlSurcharges {
  fuel: { service?: string; source?: string; imported_on?: string; weeks: DhlFuelWeek[] }
  demand: { valid_from: string; valid_to: string; origin_region: string; rates: Record<DemandRegion, number>; source?: string; imported_on?: string }
  region_labels: Record<DemandRegion, string>
  defaults: { vat_pct: number; gogreen_per_kg: number }
}

export interface DhlConfig {
  meta: { version: string; currency: string; origin: string; valid_to: string; volumetric_divisor: number; notes: string[] }
  services: { id: DhlServiceId; label: string; premium_eur: number }[]
  destinations: DhlDestination[]
  surcharges: DhlSurcharges
}

export interface ShippingQuoteInputs {
  destination: string
  shipment_date: string
  service: DhlServiceId
  fuel_source: DhlRateSource
  fuel_pct: number | null
  demand_source: DhlRateSource
  demand_per_kg: number | null
  gogreen_per_kg: number | null
  vat_pct: number | null
  remote_area: boolean
  other_eur: number
}

export interface ShippingQuoteResult {
  tariff_version: string
  currency: string
  destination: DhlDestination
  service: DhlServiceId
  service_label: string
  shipment_date: string
  pieces: {
    sequence: number
    box_code: string | null
    box_name: string | null
    dimensions_cm: number[]
    actual_kg: number
    volumetric_kg: number
    chargeable_kg: number
    handling: { type: string; label: string; amount: number }
  }[]
  actual_kg: number
  volumetric_kg: number
  chargeable_kg: number
  tariff_weight_kg: number
  rates: { fuel_pct: number; fuel_source: DhlRateSource; fuel_note?: string; demand_per_kg: number; demand_source: DhlRateSource; demand_note?: string; gogreen_per_kg: number; vat_pct: number }
  lines: { key: string; label: string; amount: number }[]
  subtotal: number
  vat: number
  total: number
}

export interface SavedShippingQuote {
  order_id: number
  plan_id: number | null
  inputs: ShippingQuoteInputs
  result: ShippingQuoteResult
  tariff_version: string
  created_at: string
}

export interface ShippingQuoteResponse { quote: SavedShippingQuote | null; stale: boolean; detected_destination: DetectedDestination | null }
