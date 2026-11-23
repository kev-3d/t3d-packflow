import { AlertTriangle, Check, ChevronDown } from 'lucide-react'
import { useId, useLayoutEffect, useRef, useState, type CSSProperties, type InputHTMLAttributes, type KeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Input } from './ui/input'

export type ComboboxInputProps = Pick<InputHTMLAttributes<HTMLInputElement>, 'id' | 'aria-label' | 'aria-required' | 'aria-invalid' | 'aria-describedby' | 'placeholder' | 'disabled'>

export interface ComboboxStatusText { label: string; title: string }

interface Props<T> extends ComboboxInputProps {
  value: string
  /** Options for the current text (already filtered and ordered). */
  options: T[]
  /** Whether the typed text exactly matches a known record; null hides the status badge. */
  matched: boolean | null
  optionKey: (option: T) => string | number
  optionLabel: (option: T) => string
  optionMeta?: (option: T) => ReactNode
  listLabel: string
  matchText: ComboboxStatusText
  missingText: ComboboxStatusText
  onChange: (text: string) => void
  onSelect: (option: T) => void
}

const LIST_MAX_HEIGHT = 236

/**
 * Free-text field with a dropdown of known records (ARIA combobox pattern).
 * Typing filters the list; picking an option fills its exact name so the backend can match it by name.
 */
export function SearchCombobox<T>({ value, options, matched, optionKey, optionLabel, optionMeta, listLabel, matchText, missingText, onChange, onSelect, ...inputProps }: Props<T>) {
  const listId = useId()
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const showList = open && options.length > 0
  // While the list is open the user is still choosing, so the match status is shown only once it closes.
  const status = !value.trim() || matched === null || showList ? null : matched ? 'match' : 'missing'
  const wrapperRef = useRef<HTMLDivElement>(null)
  const [listStyle, setListStyle] = useState<CSSProperties>()

  // The list is position: fixed so the modal's scroll container cannot clip it; it opens upwards when there is no room below.
  // It is portalled to the dialog layer (inside the focus-trapped dialog, but outside the transformed modal panel,
  // which would otherwise become the containing block for position: fixed).
  useLayoutEffect(() => {
    if (!showList) return
    const place = () => {
      const rect = wrapperRef.current?.getBoundingClientRect()
      if (!rect) return
      const below = window.innerHeight - rect.bottom - 8
      const above = rect.top - 8
      const up = below < Math.min(LIST_MAX_HEIGHT, 160) && above > below
      // At least 320px wide (narrow phone inputs would wrap every name), kept inside the viewport.
      const width = Math.max(rect.width, Math.min(320, window.innerWidth - 16))
      const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8))
      setListStyle({ position: 'fixed', left, width, maxHeight: Math.min(LIST_MAX_HEIGHT, Math.max(up ? above : below, 120)),
        ...(up ? { bottom: window.innerHeight - rect.top + 4, top: 'auto' } : { top: rect.bottom + 4 }) })
    }
    place()
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => { window.removeEventListener('scroll', place, true); window.removeEventListener('resize', place) }
  }, [showList, options.length])

  const choose = (option: T) => { onSelect(option); setOpen(false) }
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) { setOpen(true); setActive(0); return }
      const step = event.key === 'ArrowDown' ? 1 : -1
      setActive(current => (current + step + options.length) % Math.max(options.length, 1))
    } else if (event.key === 'Enter' && showList) {
      event.preventDefault(); choose(options[Math.min(active, options.length - 1)])
    } else if (event.key === 'Escape' && open) {
      event.preventDefault(); event.stopPropagation(); setOpen(false)
    }
  }

  const statusText = status === 'match' ? matchText : missingText
  return (
    <div ref={wrapperRef} className={`search-combobox${status ? ` search-combobox--${status}` : ''}`}>
      <Input {...inputProps} role="combobox" aria-autocomplete="list" aria-expanded={showList} aria-controls={listId}
        aria-activedescendant={showList ? `${listId}-${Math.min(active, options.length - 1)}` : undefined}
        autoComplete="off" value={value}
        onChange={event => { onChange(event.target.value); setOpen(true); setActive(0) }}
        onFocus={() => { setOpen(true); setActive(0) }} onBlur={() => setOpen(false)} onKeyDown={onKeyDown} />
      {status && <span className="search-combobox__status" title={statusText.title}>
        {status === 'match' ? <Check size={13} aria-hidden="true" /> : <AlertTriangle size={13} aria-hidden="true" />}
        <span>{statusText.label}</span>
      </span>}
      <ChevronDown className="search-combobox__chevron" size={15} aria-hidden="true" />
      {showList && createPortal(<ul id={listId} role="listbox" className="search-combobox__list" style={listStyle} aria-label={listLabel}>
        {options.map((option, index) => (
          <li key={optionKey(option)} id={`${listId}-${index}`} role="option" aria-selected={index === active}
            className={index === active ? 'is-active' : undefined}
            onMouseDown={event => event.preventDefault()} onMouseEnter={() => setActive(index)} onClick={() => choose(option)}>
            <span>{optionLabel(option)}</span>{optionMeta && <small>{optionMeta(option)}</small>}
          </li>
        ))}
      </ul>, wrapperRef.current?.closest('.modal-layer') ?? document.body)}
    </div>
  )
}
