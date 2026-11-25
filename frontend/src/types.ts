export type Page = 'dashboard' | 'orders' | 'checklist' | 'export' | 'catalog' | 'clients' | 'users' | 'order'

export type Role = 'admin' | 'user'

export interface AuthConfig {
  configured: boolean
  publishable_key: string
}

/** The signed-in user as the backend sees it; the role always comes from the server. */
export interface CurrentUser {
  id: number
  email: string
  full_name: string
  role: Role
  is_admin: boolean
}

export interface AppUser {
  id: number
  email: string
  full_name: string
  role: Role
  is_active: boolean
  /** Has signed in at least once (linked to a Clerk account). */
  linked: boolean
  created_at: string
  last_login_at: string | null
}

export interface NewUserInput {
  email: string
  full_name: string
  role: Role
  send_invitation: boolean
}

export interface NewUserResult {
  user: AppUser
  invitation: 'sent' | 'failed' | 'skipped'
  invitation_error?: string
}

export interface UserUpdate {
  full_name?: string
  role?: Role
  is_active?: boolean
}

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

export type PackingObjective = 'fewest_boxes' | 'lowest_dhl_cost' | 'smallest_volume'

export interface PackingOption {
  objective: PackingObjective
  label: string
  selected: boolean
  complete: boolean
  box_count: number
  total_volume_l: number
  utilization: number
  gross_weight_kg: number
  chargeable_kg: number
  tariff_weight_kg: number
  handling_eur: number
  estimated_dhl_eur: number
  boxes: { box_code: string; box_name: string; count: number }[]
}

export interface PackingResult {
  objective?: PackingObjective
  objective_label?: string
  dhl_zone?: number
  dhl_zone_assumed?: boolean
  options?: PackingOption[]
  settings?: { allowance_cm: number; gap_cm: number; use_capacities: boolean; allow_on_side: boolean; max_parcel_kg: number | null }
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
  client_requirements?: OrderRequirementsNote | null
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
  slack_notification?: 'sent' | 'skipped' | 'failed'
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
  ean?: string
  tsca_name?: string
  tsca_description?: string
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
  reference_50ml: number | null
  reference_250ml: number | null
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

/** Special handling the person packing must follow for a customer (from the "Special clients" sheet). */
export interface ClientRequirement {
  id: number
  company: string
  aliases: string[]
  client_id: number | null
  client_name: string | null
  /** Closest client when the requirement is not linked yet; the user confirms it with one click. */
  suggested_client: { id: number; customer: string; score: number } | null
  invoice_email: string
  payment: string
  pricing_requirements: string
  docs_required: string
  courier: string
  comments: string
  created_at: string
  updated_at: string
}

export type ClientRequirementInput = Pick<ClientRequirement,
  'company' | 'aliases' | 'client_id' | 'invoice_email' | 'payment' | 'pricing_requirements' | 'docs_required' | 'courier' | 'comments'>

export interface ClientRequirementsImportResult {
  imported: number; created: number; updated: number
  linked: number; suggested: number; unlinked: number
  errors: string[]
}

/** The note an order shows its packer. `suggested` means the customer name only resembles the company. */
export interface OrderRequirementsNote {
  requirement_id: number
  match: 'exact' | 'suggested'
  score: number
  company: string
  docs_required: string
  pricing_requirements: string
  courier: string
  comments: string
}

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

export type TscaCertification = '' | 'positive' | 'negative'

export interface TscaItem {
  product_id: number | null
  number: string
  name: string
  description: string
}

export interface TscaDraft {
  date: string
  reference: string
  certification: TscaCertification
  items: TscaItem[]
}

export interface TscaState {
  eligible: boolean
  destination: { code: string; name: string } | null
  saved: boolean
  confirmed: boolean
  updated_at: string | null
  draft: TscaDraft
  issues: string[]
}

export type ShipmentDocField = 'doc_pl' | 'doc_sds' | 'doc_stripe' | 'doc_tsca' | 'doc_sec232' | 'doc_coo'
export type ShipmentEmailField = 'email_client_sent' | 'email_forwarder_sent'

/** One outgoing shipment (a row of the Export tracking sheet). Document checks: null = not applicable. */
export interface Shipment {
  id: number
  ship_date: string
  order_id: number | null
  order_number: string | null
  client: string
  po: string
  destination: string
  invoice_number: string
  tracking_number: string
  forwarder: string
  doc_pl: boolean | null
  doc_sds: boolean | null
  doc_stripe: boolean | null
  doc_tsca: boolean | null
  doc_sec232: boolean | null
  doc_coo: boolean | null
  email_client_sent: boolean
  email_client_sent_at: string | null
  email_forwarder_sent: boolean
  email_forwarder_sent_at: string | null
  notes: string
  created_by: string
  created_at: string
  updated_at: string
  progress: { done: number; total: number }
  complete: boolean
  requirements: OrderRequirementsNote | null
}

export interface ShipmentInput {
  ship_date: string
  order_id?: number | null
  client?: string
  po?: string
  destination?: string
  invoice_number?: string
  tracking_number?: string
  forwarder?: string
  notes?: string
}

export type ShipmentUpdate = Partial<Omit<ShipmentInput, 'order_id'>> & Partial<Record<ShipmentDocField, boolean | null>> & Partial<Record<ShipmentEmailField, boolean>>

export interface ShipmentOrderCandidate { id: number; order_number: string; client: string; order_date: string | null; status: string }

export interface ShipmentOrderDefaults {
  order_id: number
  client: string
  po: string
  destination: string
  forwarder: string
}

export interface ShipmentFilters { from?: string; to?: string; q?: string; forwarder?: string; destination?: string; pending?: boolean }
