import { Inbox, PackageCheck } from 'lucide-react'
import { useId, useState, type DragEvent, type ReactNode } from 'react'
import type { Package } from '../../checklist/model'
import { draggedId, draggedStatus, useChecklist } from './context'
import { PackageCard } from './PackageCard'

export function EmptyState({ icon, children }: { icon?: ReactNode; children: ReactNode }) {
  return <div className="ck-empty">{icon ?? <Inbox aria-hidden="true" size={20} />}<p>{children}</p></div>
}

/** Drop-target wiring shared by Kanban and day columns. */
export function useDropTarget(accepts: (status: string) => boolean, onDropPackage: (pkg: Package) => void) {
  const { findPackage } = useChecklist()
  const [over, setOver] = useState(false)
  const accepted = (event: DragEvent) => {
    const status = draggedStatus(event)
    return status !== null && accepts(status)
  }
  return {
    over,
    handlers: {
      onDragOver: (event: DragEvent<HTMLElement>) => {
        if (!accepted(event)) return
        event.preventDefault()
        event.dataTransfer.dropEffect = 'move'
        if (!over) setOver(true)
      },
      onDragLeave: (event: DragEvent<HTMLElement>) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOver(false)
      },
      onDrop: (event: DragEvent<HTMLElement>) => {
        setOver(false)
        if (!accepted(event)) return
        event.preventDefault()
        const pkg = findPackage(Number(draggedId(event)))
        if (pkg) onDropPackage(pkg)
      },
    },
  }
}

type KanbanColumnProps = {
  kind: 'por_empacar' | 'empacados'
  title: string
  packages: Package[]
  emptyMessage: string
}

export function KanbanColumn({ kind, title, packages, emptyMessage }: KanbanColumnProps) {
  const { actions } = useChecklist()
  const headingId = useId()
  const drop = useDropTarget(
    status => (kind === 'empacados' ? status === 'por_empacar' : status === 'empacado'),
    pkg => (kind === 'empacados' ? actions.markPacked(pkg) : actions.markUnpacked(pkg)),
  )
  const count = packages.length
  return (
    <section className="ck-col" data-kind={kind} aria-labelledby={headingId}>
      <header className="ck-col__head">
        <h3 id={headingId}>{title}</h3>
        <span className="ck-count" aria-label={`${count} ${count === 1 ? 'package' : 'packages'}`}>{count}</span>
      </header>
      <div className="ck-col__body" data-focus-scope tabIndex={-1} data-drop-over={drop.over || undefined} {...drop.handlers}>
        {count ? (
          <ul className="ck-list" aria-labelledby={headingId}>
            {packages.map(pkg => <li key={pkg.id}><PackageCard pkg={pkg} placement={kind} /></li>)}
          </ul>
        ) : (
          <EmptyState icon={kind === 'empacados' ? <PackageCheck aria-hidden="true" size={20} /> : undefined}>{emptyMessage}</EmptyState>
        )}
      </div>
    </section>
  )
}
