import { createContext, useContext, type DragEvent } from 'react'
import type { Package, PickupOption, PickupSlot } from '../../checklist/model'

export type ChecklistActions = {
  markPacked: (pkg: Package) => void
  markUnpacked: (pkg: Package) => void
  assignPickup: (pkg: Package, date: string, slot: PickupSlot | null) => void
  confirmPickup: (pkg: Package) => void
}

export type ChecklistContextValue = {
  today: string
  /** Debounced search text, used for highlighting. */
  query: string
  pickupOptions: PickupOption[]
  actions: ChecklistActions
  /** Package lookup for drag & drop. */
  findPackage: (id: number) => Package | undefined
}

export const ChecklistContext = createContext<ChecklistContextValue | null>(null)

export function useChecklist(): ChecklistContextValue {
  const value = useContext(ChecklistContext)
  if (!value) throw new Error('useChecklist must be used inside <ChecklistContext.Provider>.')
  return value
}

/* ---------- drag & drop (native API, an optional shortcut for the buttons) ---------- */

const DRAG_PREFIX = 'application/x-ck-'

export function setDragPackage(event: DragEvent, pkg: Package) {
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData(`${DRAG_PREFIX}${pkg.status}`, String(pkg.id))
}

/** The dragged package's status: readable during dragover, when the data itself is not. */
export function draggedStatus(event: DragEvent): string | null {
  const type = Array.from(event.dataTransfer.types).find(t => t.startsWith(DRAG_PREFIX))
  return type ? type.slice(DRAG_PREFIX.length) : null
}

export function draggedId(event: DragEvent): string | null {
  const status = draggedStatus(event)
  return status ? event.dataTransfer.getData(`${DRAG_PREFIX}${status}`) || null : null
}

/* ---------- focus ---------- */

/**
 * Call before an action removes the card that holds `button`: once React re-renders,
 * focus lands on the card now at the same position in the scope (or the scope itself),
 * so keyboard users keep their place instead of falling back to <body>.
 */
export function keepFocusInScope(button: HTMLElement) {
  const scope = button.closest<HTMLElement>('[data-focus-scope]')
  if (!scope) return
  const selector = '[data-card-action]:not(:disabled)'
  const index = Array.from(scope.querySelectorAll<HTMLElement>(selector)).indexOf(button)
  // A macrotask runs after React's synchronous commit of the click update (rAF would stall in background tabs).
  setTimeout(() => {
    if (document.activeElement && document.activeElement !== document.body && document.contains(document.activeElement)) return
    const actions = scope.querySelectorAll<HTMLElement>(selector)
    const target = actions[Math.min(Math.max(index, 0), actions.length - 1)] ?? scope
    target.focus({ preventScroll: false })
  })
}
