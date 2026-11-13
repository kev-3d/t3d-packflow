import { ClipboardList, TriangleAlert } from 'lucide-react'
import type { OrderRequirementsNote } from '../types'

const sections: { field: 'docs_required' | 'pricing_requirements' | 'courier' | 'comments'; label: string; wide?: boolean }[] = [
  { field: 'docs_required', label: 'Docs required', wide: true },
  { field: 'courier', label: 'Courier' },
  { field: 'pricing_requirements', label: 'Pricing / invoice', wide: true },
  { field: 'comments', label: 'Comments', wide: true },
]

/**
 * Packing note for customers with special requirements. It stays on screen (no dismiss button) so the
 * person packing the order sees it while reviewing and while checking the calculated plan.
 */
export function ClientRequirementsNote({ note }: { note: OrderRequirementsNote }) {
  const suggested = note.match === 'suggested'
  const filled = sections.filter(({ field }) => note[field])
  return (
    <section className={`requirements-note ${suggested ? 'requirements-note--suggested' : ''}`} role="note" aria-labelledby="requirements-note-title">
      <header>
        {suggested ? <TriangleAlert size={20} aria-hidden="true" /> : <ClipboardList size={20} aria-hidden="true" />}
        <h2 id="requirements-note-title">Packing note · special requirements for {note.company}</h2>
        <span>Pay attention when packing</span>
      </header>
      {suggested && (
        <p className="requirements-note__warning">
          The customer name on this order only resembles “{note.company}”. Confirm it is the same customer before following this note.
        </p>
      )}
      <dl>
        {filled.map(({ field, label, wide }) => (
          <div key={field} className={wide ? 'wide' : undefined}>
            <dt>{label}</dt>
            <dd className={wide ? 'wide' : undefined}>{note[field]}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
