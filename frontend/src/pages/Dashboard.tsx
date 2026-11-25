import { Badge } from '../components/ui/badge'
import { useEffect, useState } from 'react'
import { ArrowRight, Box, ChevronRight, ClipboardList, Package, Scale } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from 'recharts'
import { api } from '../api'
import { statusLabel } from '../workflow'
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card'
import { Button } from '../components/ui/button'
import { ChartContainer, ChartTooltip, ChartTooltipContent } from '../components/ui/chart'
import { Gauge } from '../components/charts/gauge'
import { PackingPreview } from '../components/PackingPreview'
import { ChecklistOverviewCard } from '../components/checklist/ChecklistOverviewCard'
import type { DashboardData, OrderDetailData } from '../types'

interface DashboardProps {
  data: DashboardData
  onOpenOrder: (id: number) => void
  onUpload: () => void
  onViewOrders: () => void
  onOpenChecklist: () => void
}

export function Dashboard({ data, onOpenOrder, onUpload, onViewOrders, onOpenChecklist }: DashboardProps) {
  const latest = data.orders.find(order => (order.box_count ?? 0) > 0)
  const [detail, setDetail] = useState<OrderDetailData | null>(null)
  const [previewError, setPreviewError] = useState(false)
  useEffect(() => {
    let active = true
    setDetail(null); setPreviewError(false)
    if (latest) api.order(latest.id).then(result => { if (active) setDetail(result) }).catch(() => { if (active) setPreviewError(true) })
    return () => { active = false }
  }, [latest?.id, data])
  const plan = detail?.plan?.result
  const series = data.utilization_series.filter(point => data.orders.some(order => order.order_number === point.label && (order.box_count ?? 0) > 0)).slice(-7)
  const metrics = [
    { label: 'Average utilization', value: `${data.kpis.average_utilization}%`, caption: 'Latest plan per order', icon: Package },
    { label: 'Orders on record', value: data.kpis.orders, caption: `${data.kpis.ready} packing plans ready`, icon: ClipboardList },
    { label: 'Boxes assigned', value: data.kpis.boxes, caption: `${data.kpis.orders ? (data.kpis.boxes / data.kpis.orders).toFixed(1) : '0'} avg. per order`, icon: Box },
    { label: 'Planned weight', value: `${data.kpis.total_weight_kg} kg`, caption: 'Across saved plans', icon: Scale },
  ]
  return <div className="page dashboard-page">
    <header className="page-header"><div><h1>Dashboard</h1><p>Organize your orders and make the most of every box.</p></div></header>
    <Card className="efficiency-card">
      <div className="efficiency-chart"><h2>Packing efficiency</h2>
        {series.length ? <ChartContainer config={{value:{label:'Utilization',color:'#242424'}}} className="h-44 w-full" aria-label="Utilization of the latest calculated orders">
          <BarChart accessibilityLayer data={series} margin={{top:12,right:8,left:-22,bottom:0}}>
            <CartesianGrid vertical={false} stroke="#E5E5E3" />
            <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={10} tickFormatter={value => String(value).replace('PO-','#')} />
            <YAxis domain={[0,100]} ticks={[0,25,50,75,100]} tickFormatter={value => `${value}%`} tickLine={false} axisLine={false}/>
            <ChartTooltip cursor={{fill:'#EDEDEB'}} content={<ChartTooltipContent formatter={(value) => <span>Utilization <b>{value}%</b></span>} />} />
            <Bar dataKey="value" fill="var(--color-value)" radius={[4,4,0,0]} maxBarSize={28}/>
          </BarChart>
        </ChartContainer> : <p className="dashboard-empty">Calculate your first plan to see packing efficiency.</p>}
        <p className="chart-caption">Latest {series.length || 'available'} plans · target 85%</p>
      </div>
      <div className="efficiency-gauge" role="img" aria-label={`Average utilization: ${data.kpis.average_utilization} percent. Mean of latest plans per order; orders without a plan excluded.`}>
        <Gauge value={data.kpis.average_utilization} startAngle={180} endAngle={360} totalNotches={60} spacing={65} notchLengthPercent={55} activeFill="#242424" inactiveFill="#E5E5E3" inactiveFillOpacity={1} width={280} height={280} />
        <div className="gauge-label"><strong>{data.kpis.average_utilization}%</strong><span>Average utilization</span></div>
      </div>
      <div className="efficiency-facts">{metrics.slice(1).map(metric => <div key={metric.label}><strong>{metric.value}</strong><span>{metric.label}</span></div>)}</div>
    </Card>
    <section className="overview-metrics" aria-label="Workspace metrics">{metrics.map(({label,value,caption,icon:Icon}) => <Card key={label} className="metric-card"><span className="metric-icon"><Icon size={25} strokeWidth={1.6}/></span><div><h2>{label}</h2><strong>{value}</strong><p>{caption}</p></div></Card>)}</section>
    <div className="overview-panels">
      <Card className="recent-card"><CardHeader><CardTitle>Recent orders</CardTitle></CardHeader><CardContent className="px-5 pb-0">
        {!data.orders.length && <div className="dashboard-empty"><p>Your next shipment starts here.</p><Button onClick={onUpload}>Create an order</Button></div>}
        {data.orders.slice(0,3).map(order => <button className="recent-order" key={order.id} onClick={() => onOpenOrder(order.id)}><span className="recent-icon"><Box size={21}/></span><span className="recent-description"><b>{order.order_number}</b><small title={order.client}>{order.client}</small></span><span className="recent-state"><b>{order.box_count ? `${order.average_utilization}%` : '—'}</b><Badge className={`status-pill status-pill--${order.status}`}>{statusLabel(order.status)}</Badge></span><ChevronRight size={16}/></button>)}
        <Button variant="ghost" className="mt-4 w-full justify-between" onClick={onViewOrders}>View all orders <ArrowRight size={16}/></Button>
      </CardContent></Card>
      <Card className="latest-plan-card"><CardHeader><CardTitle>Last packing plan {latest && <span>· {latest.order_number}</span>}</CardTitle><p>{plan ? `${plan.box_count} ${plan.box_count === 1 ? 'box' : 'boxes'} · ${plan.unplaced.length ? `${plan.unplaced.length} unplaced units` : 'All units placed'}` : 'Your latest calculated shipment'}</p></CardHeader><CardContent className="px-5">
        {plan?.boxes[0] ? <><button className="preview-button" onClick={() => onOpenOrder(detail!.id)} aria-label={`Open packing plan ${detail!.order_number}`}><PackingPreview box={plan.boxes[0]}/><span>Explore packing plan <ArrowRight size={15}/></span></button><div className="preview-metrics"><div><b>{plan.box_count}</b><span>Boxes</span></div><div><b>{plan.average_utilization}%</b><span>Utilization</span></div><div><b>{plan.total_weight_kg} kg</b><span>Total weight</span></div></div></> : <div className="dashboard-empty"><Box size={44}/><p>{previewError ? 'Preview unavailable. You can still open the order.' : latest ? 'Loading your packing plan…' : 'Your first packing plan will appear here.'}</p><Button variant="secondary" onClick={() => latest ? onOpenOrder(latest.id) : onUpload()}>{latest ? 'Open order' : 'Create an order'}</Button></div>}
      </CardContent></Card>
      <ChecklistOverviewCard onOpenChecklist={onOpenChecklist} />
    </div>
  </div>
}
