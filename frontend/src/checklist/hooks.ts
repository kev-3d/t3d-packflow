import { useEffect, useLayoutEffect, useState, type RefObject } from 'react'
import { toIsoDate } from './model'

/** Today's local date (`YYYY-MM-DD`); rolls over at midnight while the screen stays open. */
export function useToday(): string {
  const [today, setToday] = useState(() => toIsoDate(new Date()))
  useEffect(() => {
    const timer = setInterval(() => setToday(toIsoDate(new Date())), 60_000)
    return () => clearInterval(timer)
  }, [])
  return today
}

export function useDebouncedValue<T>(value: T, delay = 150): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}

/** Exposes the element's distance from the viewport top as `--ck-top`, so CSS can fill the rest of the screen. */
export function useViewportOffset(ref: RefObject<HTMLElement | null>) {
  useLayoutEffect(() => {
    const element = ref.current
    if (!element) return
    const measure = () => element.style.setProperty('--ck-top', `${element.getBoundingClientRect().top + window.scrollY}px`)
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [ref])
}

/** Focuses the "/"-shortcut target unless the user is already typing somewhere. */
export function useSlashShortcut(ref: RefObject<HTMLInputElement | null>) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) return
      const target = event.target as HTMLElement | null
      if (target && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))) return
      event.preventDefault()
      ref.current?.focus()
      ref.current?.select()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [ref])
}
