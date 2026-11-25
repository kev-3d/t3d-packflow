import type { RecommendationPreview } from './workflow'
import type { ApiChecklistPackage, ApiPickupSlot } from './checklist/model'
import type { AnalysisReport, AppUser, AuthConfig, BoxType, Client, ClientImportResult, ClientInput, ClientRequirement, ClientRequirementInput, ClientRequirementsImportResult, CurrentUser, DashboardData, DhlConfig, NewUserInput, NewUserResult, OrderDetailData, OrderSummary, Product, ShippingQuoteInputs, ShippingQuoteResponse, Shipment, ShipmentFilters, ShipmentInput, ShipmentOrderCandidate, ShipmentOrderDefaults, ShipmentUpdate, TscaDraft, TscaState, UserUpdate } from './types'

export class ApiError extends Error {
  constructor(message: string, public readonly status: number, public readonly details: unknown) { super(message); this.name = 'ApiError' }
}

// The signed-in session (Clerk) provides a short-lived token for every API call.
type TokenProvider = () => Promise<string | null>
let tokenProvider: TokenProvider | null = null
let unauthorizedHandler: (() => void) | null = null

export function configureAuth(provider: TokenProvider | null, onUnauthorized: (() => void) | null) {
  tokenProvider = provider
  unauthorizedHandler = onUnauthorized
}

/** fetch() with the session token; a 401 means the session ended, so sign out. */
async function authFetch(url: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers)
  const token = tokenProvider ? await tokenProvider() : null
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(url, { ...init, headers })
  if (response.status === 401) unauthorizedHandler?.()
  return response
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await authFetch(`/api${path}`, options)
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: 'The request could not be completed.' }))
    throw new ApiError(typeof payload.detail === 'string' ? payload.detail : 'Some fields need attention. Review the highlighted values.', response.status, payload.detail)
  }
  return response.json() as Promise<T>
}

const safeName = (orderNumber: string, id: number) => orderNumber.replace(/[^a-zA-Z0-9_-]/g, '') || String(id)

/** Fetch a generated PDF and hand it to the browser as a download. */
async function downloadPdf(url: string, filename: string, fallbackError: string) {
  const response = await authFetch(url)
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: fallbackError }))
    throw new Error(payload.detail || fallbackError)
  }
  const objectUrl = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  // Allow the browser to start consuming the file before releasing the URL.
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
}

const jsonBody = (method: string, payload: object): RequestInit =>
  ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })

export const api = {
  /** Public: needed before anyone signs in, so it never sends a token. */
  authConfig: async (): Promise<AuthConfig> => {
    const response = await fetch('/api/auth/config')
    if (!response.ok) throw new ApiError('The server is not available.', response.status, null)
    return response.json() as Promise<AuthConfig>
  },
  me: () => request<CurrentUser>('/auth/me'),
  users: () => request<AppUser[]>('/admin/users'),
  createUser: (payload: NewUserInput) => request<NewUserResult>('/admin/users', jsonBody('POST', payload)),
  updateUser: (id: number, payload: UserUpdate) => request<AppUser>(`/admin/users/${id}`, jsonBody('PATCH', payload)),
  resendInvitation: (id: number) => request<Pick<NewUserResult, 'invitation' | 'invitation_error'>>(`/admin/users/${id}/invitation`, { method: 'POST' }),
  dashboard: () => request<DashboardData>('/dashboard'),
  orders: () => request<OrderSummary[]>('/orders'),
  order: (id: number) => request<OrderDetailData>(`/orders/${id}`),
  uploadOrder: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return request<OrderDetailData>('/orders/upload', { method: 'POST', body })
  },
  createManualOrder: (payload: object) =>
    request<OrderDetailData>('/orders/manual', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  createEmailOrder: (payload: { text: string; client?: string; subject?: string }) =>
    request<OrderDetailData>('/orders/email', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  savePackingPlan: (id: number) => request<AnalysisReport>(`/orders/${id}/reports`, { method: 'POST' }),
  deleteAllOrders: () => request<{ deleted_orders: number }>('/orders', { method: 'DELETE' }),
  deleteOrder: (id: number) => request<{ deleted_order_id: number; order_number: string }>(`/orders/${id}`, { method: 'DELETE' }),
  reviewOrder: (id: number, payload: object) =>
    request<OrderDetailData>(`/orders/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  analyzeOrder: (id: number, objective?: string, allowanceCm?: number) => {
    const query = new URLSearchParams()
    if (objective) query.set('objective', objective)
    if (allowanceCm !== undefined) query.set('allowance_cm', String(allowanceCm))
    const suffix = query.toString() ? `?${query}` : ''
    return request(`/orders/${id}/analyze${suffix}`, { method: 'POST' })
  },
  addSuggestedItem: (orderId: number, sourceItemId: number, quantity: number, expectedPlanId?: number) =>
    request<OrderDetailData>(`/orders/${orderId}/suggested-items`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_item_id: sourceItemId, quantity, expected_plan_id: expectedPlanId }),
    }),
  removeSuggestedItem: (orderId: number, itemId: number, quantity?: number) =>
    request<OrderDetailData>(`/orders/${orderId}/suggested-items/${itemId}${quantity ? `?quantity=${quantity}` : ''}`, { method: 'DELETE' }),
  exportOrder: async (id: number, orderNumber: string) => {
    const response = await authFetch(`/api/orders/${id}/export`)
    if (!response.ok) throw new Error('The plan could not be exported.')
    const url = URL.createObjectURL(await response.blob())
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `plan-${orderNumber}.xlsx`
    anchor.click()
    URL.revokeObjectURL(url)
  },
  exportOrderPdf: async (id: number, orderNumber: string) => {
    const response = await authFetch(`/api/orders/${id}/export.pdf`)
    if (!response.ok) {
      const payload = await response.json().catch(() => ({ detail: 'The PDF could not be exported.' }))
      throw new Error(payload.detail || 'The PDF could not be exported.')
    }
    const url = URL.createObjectURL(await response.blob())
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `plan-${orderNumber.replace(/[^a-zA-Z0-9_-]/g, '') || id}.pdf`
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    // Allow the browser to start consuming the file before releasing the URL.
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
  },
  previewRecommendation: (id: number, sourceItemId: number, quantity: number, planId: number) =>
    request<RecommendationPreview>(`/orders/${id}/recommendations/preview`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({source_item_id:sourceItemId,quantity,expected_plan_id:planId}) }),
  packingListPdf: (id: number, orderNumber: string) =>
    downloadPdf(`/api/orders/${id}/packing-list.pdf`, `packing-list-${safeName(orderNumber, id)}.pdf`, 'The packing list could not be generated.'),
  boxLabelsPdf: (id: number, orderNumber: string, box?: number) =>
    downloadPdf(`/api/orders/${id}/labels.pdf${box ? `?box=${box}` : ''}`,
      `labels-${safeName(orderNumber, id)}${box ? `-box${box}` : ''}.pdf`, 'The box labels could not be generated.'),
  tsca: (id: number) => request<TscaState>(`/orders/${id}/tsca`),
  saveTsca: (id: number, draft: TscaDraft, confirmed: boolean, rememberProductIds: number[] = []) =>
    request<TscaState>(`/orders/${id}/tsca`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ draft, confirmed, remember_product_ids: rememberProductIds }),
    }),
  tscaPreview: async (id: number, draft: TscaDraft, confirmed: boolean, signal?: AbortSignal) => {
    const response = await authFetch(`/api/orders/${id}/tsca/preview.pdf`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ draft, confirmed }), signal,
    })
    if (!response.ok) {
      const payload = await response.json().catch(() => ({ detail: 'The preview could not be generated.' }))
      throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Some fields need attention before the preview can be shown.')
    }
    return response.blob()
  },
  tscaPdf: (id: number, orderNumber: string, final: boolean) =>
    downloadPdf(`/api/orders/${id}/tsca.pdf?final=${final ? 1 : 0}`,
      `TSCA_Certification_${safeName(orderNumber, id)}${final ? '' : '_DRAFT'}.pdf`, 'The TSCA certification could not be generated.'),
  sourcePdf: async (id: number) => {
    const response = await authFetch(`/api/orders/${id}/source.pdf`)
    if (!response.ok) throw new Error(response.status === 404 ? 'The original PDF is unavailable. Only PDFs uploaded after source retention was enabled can be viewed.' : 'The source PDF could not be opened. Try again.')
    return response.blob()
  },
  products: () => request<Product[]>('/catalog/products'),
  boxes: () => request<BoxType[]>('/catalog/boxes'),
  createProduct: (payload: object) =>
    request<Product>('/catalog/products', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  updateProduct: (id: number, payload: object) =>
    request<Product>(`/catalog/products/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  updateBox: (id: number, payload: object) =>
    request<BoxType>(`/catalog/boxes/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  deleteProduct: (id: number) => request<{ deleted_product_id: number; name: string }>(`/catalog/products/${id}`, { method: 'DELETE' }),
  deleteBox: (id: number) => request<{ deleted_box_id: number; code: string }>(`/catalog/boxes/${id}`, { method: 'DELETE' }),
  createBox: (payload: object) =>
    request<BoxType>('/catalog/boxes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  importCatalog: (type: 'products' | 'boxes', file: File) => {
    const body = new FormData()
    body.append('file', file)
    return request<{ imported: number; imported_products?: number; imported_boxes?: number; reference_workbook?: boolean; errors: string[] }>(`/catalog/${type}/import`, {
      method: 'POST',
      body,
    })
  },
  clients: () => request<Client[]>('/clients'),
  importClients: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return request<ClientImportResult>('/clients/import', { method: 'POST', body })
  },
  updateClient: (id: number, payload: ClientInput) =>
    request<Client>(`/clients/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  deleteClient: (id: number) => request<{ deleted_client_id: number; customer: string }>(`/clients/${id}`, { method: 'DELETE' }),
  deleteAllClients: () => request<{ deleted_clients: number }>('/clients', { method: 'DELETE' }),
  clientRequirements: () => request<ClientRequirement[]>('/client-requirements'),
  importClientRequirements: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return request<ClientRequirementsImportResult>('/client-requirements/import', { method: 'POST', body })
  },
  updateClientRequirement: (id: number, payload: ClientRequirementInput) =>
    request<ClientRequirement>(`/client-requirements/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  deleteClientRequirement: (id: number) => request<{ deleted_requirement_id: number; company: string }>(`/client-requirements/${id}`, { method: 'DELETE' }),
  deleteAllClientRequirements: () => request<{ deleted_requirements: number }>('/client-requirements', { method: 'DELETE' }),
  dhlConfig: () => request<DhlConfig>('/shipping/dhl'),
  shippingQuote: (orderId: number) => request<ShippingQuoteResponse>(`/orders/${orderId}/shipping-quote`),
  saveShippingQuote: (orderId: number, payload: ShippingQuoteInputs) =>
    request<ShippingQuoteResponse>(`/orders/${orderId}/shipping-quote`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  shipments: (filters: ShipmentFilters = {}) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(filters)) if (value) params.set(key, String(value))
    const query = params.toString()
    return request<Shipment[]>(`/shipments${query ? `?${query}` : ''}`)
  },
  shipmentOrderCandidates: () => request<ShipmentOrderCandidate[]>('/shipments/order-candidates'),
  shipmentOrderDefaults: (orderId: number) => request<ShipmentOrderDefaults>(`/shipments/order-defaults/${orderId}`),
  createShipment: (payload: ShipmentInput) => request<Shipment>('/shipments', jsonBody('POST', payload)),
  updateShipment: (id: number, payload: ShipmentUpdate) => request<Shipment>(`/shipments/${id}`, jsonBody('PATCH', payload)),
  deleteShipment: (id: number) => request<{ deleted_shipment_id: number }>(`/shipments/${id}`, { method: 'DELETE' }),
  checklist: () => request<ApiChecklistPackage[]>('/checklist'),
  checklistAction: (id: number, payload: ChecklistActionInput) => request<ApiChecklistPackage>(`/checklist/${id}`, jsonBody('PATCH', payload)),
}

export type ChecklistActionInput =
  | { action: 'pack' | 'unpack' | 'pick_up' | 'undo_pick_up' }
  | { action: 'assign_pickup'; pickup_date: string; pickup_slot: ApiPickupSlot | null }
