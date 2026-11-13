import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { Maximize, X } from 'lucide-react'
import { useDialogFocus } from '../hooks/useDialogFocus'
import type { PackedBox } from '../types'
import { heightLevels, type BoxSceneHandle, type HeightLayer } from './box3d/sceneBuilder'

// three.js is only downloaded when a packing plan is actually shown.
const BoxScene = lazy(() => import('./box3d/BoxScene'))

/** `product` is the highlighted product SKU; the product chips that set it live in the box inspector card. */
export function BoxVisual({ box, product, onProductChange }: { box: PackedBox; product: string; onProductChange: (sku: string) => void }) {
    // Rotate and zoom happen directly in the scene (drag / scroll); only Labels and Expand are exposed as controls.
    const [labels, setLabels] = useState(false), [layer, setLayer] = useState<HeightLayer>('all'), [selected, setSelected] = useState(''), [expanded, setExpanded] = useState(false)
    const setProduct = onProductChange
    // Highlighting a product from the inspector clears any single inspected unit, as the old legend did.
    useEffect(() => { if (product) setSelected('') }, [product])
    const ref = useDialogFocus<HTMLDivElement>(() => setExpanded(false), false, expanded)
    const scene = useRef<BoxSceneHandle>(null)
    const levels = heightLevels(box)
    const unit = box.placements.find(p => p.uid === selected)
    return <div ref={ref} className={`box-visual-wrap py-4 interactive-viewer ${expanded ? 'viewer--expanded' : ''}`} role={expanded ? 'dialog' : undefined} aria-modal={expanded ? true : undefined} aria-label={`Box ${box.sequence} placement viewer`}>
        <div className="viewer-filters">
           {/* <label>Height slice<select value={layer} onChange={e => setLayer(e.target.value === 'all' ? 'all' : Number(e.target.value))}>
                <option value="all">All heights</option>{levels.map(z => <option key={z} value={z}>Z = {z} cm</option>)}
            </select>
            </label>
            <label>Inspect a unit<select value={selected} onChange={e => { setSelected(e.target.value); setProduct('') }}>
                <option value="">Select a unit</option>{box.placements.map(p => <option key={p.uid} value={p.uid}>{p.name} · {p.uid}</option>)}</select>
            </label> 
        */}
        </div>
        <div className="box-stage py-6 box3d-stage" role="region" aria-label="Packing layout 3D view">
            <div className="viewer-overlay-controls" aria-label="Viewer controls">
                <label><input type="checkbox" checked={labels} onChange={e => setLabels(e.target.checked)} />Labels</label>
                <button type="button" onClick={() => setExpanded(e => !e)} aria-label={expanded ? 'Close expanded viewer' : 'Expand viewer'} title={expanded ? 'Close expanded viewer' : 'Expand viewer'}>{expanded ? <X size={16} /> : <Maximize size={16} />}</button>
            </div>
            <Suspense fallback={<p className="box3d-loading" role="status">Loading 3D view…</p>}>
                <BoxScene ref={scene} box={box} top={false} labels={labels} layer={layer} selected={selected} product={product} onSelect={uid => { setSelected(uid); setProduct('') }} onZoomChange={() => {}} />
            </Suspense>
        </div>
        {unit && <dl className="viewer-unit-details" aria-live="polite"><div><dt>Selected unit</dt><dd>{unit.name} · {unit.uid}</dd></div><div><dt>Position x, y, z (cm)</dt><dd>{unit.position.join(', ')}</dd></div><div><dt>Oriented dimensions (cm)</dt><dd>{unit.dimensions.join(' × ')}</dd></div></dl>}

    </div>
}
