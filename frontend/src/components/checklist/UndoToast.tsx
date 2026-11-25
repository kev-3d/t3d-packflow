import { RotateCcw, Truck, X } from 'lucide-react'

type UndoToastProps = { tracking: string; onUndo: () => void; onDismiss: () => void }

export function UndoToast({ tracking, onUndo, onDismiss }: UndoToastProps) {
  return (
    <div className="ck-toast" role="status">
      <Truck aria-hidden="true" size={18} />
      <span><b>{tracking}</b> picked up</span>
      <button type="button" className="ck-btn ck-btn--toast" onClick={onUndo}><RotateCcw aria-hidden="true" size={15} />Undo</button>
      <button type="button" className="ck-toast__close" aria-label="Dismiss notice" onClick={onDismiss}><X aria-hidden="true" size={16} /></button>
    </div>
  )
}
