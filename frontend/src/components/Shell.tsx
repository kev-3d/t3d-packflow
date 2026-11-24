import { UserButton } from '@clerk/react'
import { Bell, Boxes, Building2, FileStack, Home, Plus, Search, Users } from 'lucide-react'
import { useState, type KeyboardEvent, type ReactNode } from 'react'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Menubar, MenubarMenu, MenubarTrigger } from './ui/menubar'
import { useCurrentUser } from '../auth'
import type { OrderSummary, Page } from '../types'

interface ShellProps {
  activePage: Page
  onNavigate: (page: Page) => void
  onUpload: () => void
  orders: OrderSummary[]
  onOpenOrder: (id: number) => void
  children: ReactNode
}

const navigation = [
  { id: 'dashboard' as const, label: 'Overview', icon: Home },
  { id: 'orders' as const, label: 'Purchase orders', icon: FileStack },
  { id: 'catalog' as const, label: 'Products and boxes', icon: Boxes },
  { id: 'clients' as const, label: 'Clients', icon: Building2 },
  { id: 'users' as const, label: 'Users', icon: Users, adminOnly: true },
]

export function Shell({ activePage, onNavigate, onUpload, orders, onOpenOrder, children }: ShellProps) {
  const { is_admin: isAdmin } = useCurrentUser()
  const [query, setQuery] = useState('')
  const matches = query.trim() ? orders.filter(order => `${order.order_number} ${order.client}`.toLowerCase().includes(query.toLowerCase())).slice(0,5) : []
  const pendingCount = orders.filter(order => order.status !== 'packed').length

  // The sections are direct destinations, so the menubar never opens a dropdown:
  // it keeps its roving arrow-key focus, and Enter/Space navigate like a click.
  const onTriggerKeyDown = (page: Page) => (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onNavigate(page) }
    else if (event.key === 'ArrowDown') event.preventDefault()
  }

  return (
    <div className="app-shell app-shell--topnav">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="topbar">
        <div className="topbar-brand">
          <img className="brand-symbol brand-logo" src="/logo.jpg" width={32} height={32} alt="Thought3D logo" />
          <div><strong>PackFlow<span style={{ color: 'var(--brand-orange)' }}>.</span></strong><small>by Thought3D</small></div>
        </div>
        <nav className="topbar-nav" aria-label="Main navigation">
          <Menubar value="" onValueChange={() => {}} className="topbar-menubar">
            {navigation.filter(item => isAdmin || !item.adminOnly).map(({ id, label, icon: Icon }) => {
              const active = activePage === id || (id === 'orders' && activePage === 'order')
              return <MenubarMenu key={id} value={id}>
                <MenubarTrigger
                  className="topbar-menubar-trigger"
                  data-active={active || undefined}
                  aria-current={active ? 'page' : undefined}
                  aria-haspopup={undefined}
                  aria-expanded={undefined}
                  onClick={() => onNavigate(id)}
                  onKeyDown={onTriggerKeyDown(id)}
                ><Icon size={17} strokeWidth={1.8} aria-hidden="true"/><span>{label}</span></MenubarTrigger>
              </MenubarMenu>
            })}
          </Menubar>
        </nav>
        <div className="workspace-tools">
          <div className="workspace-search"><Search size={18}/><Input aria-label="Search orders" placeholder="Search orders, customers…" value={query} onChange={event=>setQuery(event.target.value)} onKeyDown={event=>{if(event.key==='Escape')setQuery('')}}/>{query && <div className="search-results"><p role="status">{matches.length ? 'Matching orders' : 'No matching orders'}</p>{matches.map(order=><button key={order.id} onClick={()=>{setQuery('');onOpenOrder(order.id)}}><b>{order.order_number}</b><span>{order.client}</span></button>)}<Button variant="ghost" onClick={()=>setQuery('')}>Close search</Button></div>}</div>
          <Button variant="secondary" size="icon" aria-label={`View orders: ${pendingCount} need attention`} onClick={()=>onNavigate('orders')} className="notification-button"><Bell size={19}/>{pendingCount>0 && <i/>}</Button>
          <Button onClick={onUpload} className="new-order-button" aria-label="New order"><Plus size={16}/><span>New order</span></Button>
          <div className="topbar-user"><UserButton /></div>
        </div>
      </header>
      <main id="main-content" className="main-canvas" tabIndex={-1}>
        {children}
      </main>
    </div>
  )
}
