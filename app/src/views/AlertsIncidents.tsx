/**
 * Copyright (c) 2026 OpenNVR
 * This file is part of OpenNVR.
 * 
 * OpenNVR is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 * 
 * OpenNVR is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 * 
 * You should have received a copy of the GNU Affero General Public License
 * along with OpenNVR.  If not, see <https://www.gnu.org/licenses/>.
 */

import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useVirtualizer } from '@tanstack/react-virtual'
import { apiService } from '../lib/apiService'

type EveAlert = any

function useQuery() {
  const { search } = useLocation()
  return useMemo(() => new URLSearchParams(search), [search])
}

export function AlertsIncidents() {
  const query = useQuery()
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [items, setItems] = useState<EveAlert[]>([])

  const onlyAlerts = query.get('only_alerts') === '1' || query.get('only_alerts') === 'true'
  const severity = query.get('severity') // Suricata: 1=high, 2=med, 3=low
  const category = query.get('category')

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setError(null)
      try {
        const { data } = await apiService.getSuricataEveLogs({ limit: 5000, only_alerts: true })
        const all = (data?.items || []) as EveAlert[]
        let filtered = all
        if (onlyAlerts) {
          filtered = filtered.filter((e: any) => e?.event_type === 'alert')
        }
        if (severity) {
          filtered = filtered.filter((e: any) => String(e?.alert?.severity) === String(severity))
        }
        if (category) {
          filtered = filtered.filter((e: any) => (e?.alert?.category || '').toLowerCase() === String(category).toLowerCase())
        }
        // sort by timestamp desc if present
        filtered = filtered.slice().sort((a: any, b: any) => {
          const ta = a?.timestamp ? Date.parse(String(a.timestamp).replace('+0000', '+00:00').replace('Z', '+00:00')) : 0
          const tb = b?.timestamp ? Date.parse(String(b.timestamp).replace('+0000', '+00:00').replace('Z', '+00:00')) : 0
          return tb - ta
        })
        if (!cancelled && filtered.length > 0) {
          setItems(filtered)
          setLoading(false)
          return
        }
        // Fallback to fast.log if eve alerts empty
        const fast = await apiService.getSuricataFastLogs({ limit: 5000 })
        const mapped = (fast?.data?.items || []).map((it: any) => ({
          timestamp: it?.timestamp || it?.ts || '',
          alert: {
            severity: it?.priority,
            category: it?.classification,
            signature: it?.signature,
          },
          src_ip: it?.src_ip,
          src_port: it?.src_port,
          dest_ip: it?.dst_ip,
          dest_port: it?.dst_port,
          event_type: 'alert',
        }))
        let f = mapped as any[]
        if (severity) f = f.filter((e: any) => String(e?.alert?.severity) === String(severity))
        if (category) f = f.filter((e: any) => (e?.alert?.category || '').toLowerCase() === String(category).toLowerCase())
        f = f.slice().sort((a: any, b: any) => Date.parse(a.timestamp || '0') - Date.parse(b.timestamp || '0')).reverse()
        if (!cancelled) setItems(f)
      } catch (e: any) {
        if (!cancelled) setError(e?.message || 'Failed to load alerts')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [onlyAlerts, severity, category])

  const setParam = (key: string, value: string | null) => {
    const p = new URLSearchParams(query as any)
    if (value === null) p.delete(key)
    else p.set(key, value)
    navigate({ search: p.toString() })
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h1 className="text-xl font-semibold">Alerts &amp; Incidents</h1>
        {loading && <span className="text-xs text-[var(--text-dim)]">Loading…</span>}
        {error && <span className="text-xs text-red-400">{error}</span>}
      </div>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-[var(--text-dim)]">Filters:</span>
        <button
          className={`px-2 py-1 rounded border ${onlyAlerts ? 'bg-[var(--panel-2)] border-[var(--border)]' : 'border-neutral-700'}`}
          onClick={() => setParam('only_alerts', onlyAlerts ? null : '1')}
        >
          Alerts only
        </button>
        <button
          className={`px-2 py-1 rounded border ${severity === '1' ? 'bg-[var(--panel-2)] border-[var(--border)]' : 'border-neutral-700'}`}
          onClick={() => setParam('severity', severity === '1' ? null : '1')}
        >
          High
        </button>
        <button
          className={`px-2 py-1 rounded border ${severity === '2' ? 'bg-[var(--panel-2)] border-[var(--border)]' : 'border-neutral-700'}`}
          onClick={() => setParam('severity', severity === '2' ? null : '2')}
        >
          Medium
        </button>
        <button
          className={`px-2 py-1 rounded border ${severity === '3' ? 'bg-[var(--panel-2)] border-[var(--border)]' : 'border-neutral-700'}`}
          onClick={() => setParam('severity', severity === '3' ? null : '3')}
        >
          Low
        </button>
        {category && (
          <button
            className="px-2 py-1 rounded border border-neutral-700"
            onClick={() => setParam('category', null)}
          >
            Category: {category} ×
          </button>
        )}
      </div>

      <div className="flex items-center justify-between text-xs text-[var(--text-dim)]">
        <span>Showing {items.length} alert{items.length === 1 ? '' : 's'}</span>
        {(severity || category) && <span>Active filters: {severity ? `severity=${severity}` : ''} {category ? `category=${category}` : ''}</span>}
      </div>

      <VirtualAlertTable items={items} loading={loading} onCategoryClick={(c) => setParam('category', c)} />
    </div>
  )
}

// Suricata can return thousands of rows; render only the visible window.
const GRID_COLS = 'grid grid-cols-[170px_90px_200px_minmax(240px,1fr)_160px_160px]'
const ROW_HEIGHT = 33

function VirtualAlertTable({ items, loading, onCategoryClick }: { items: EveAlert[]; loading: boolean; onCategoryClick: (category: string) => void }) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const rowVirtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
  })

  return (
    <div ref={scrollRef} className="overflow-auto border border-[var(--border)] bg-[var(--panel-2)] rounded max-h-[calc(100vh-19rem)] min-h-48">
      <div className="min-w-[1020px] text-xs">
        <div className={`${GRID_COLS} sticky top-0 z-10 bg-[var(--bg-2)] text-[var(--text-dim)] border-b border-[var(--border)]`}>
          <div className="p-2 font-medium">Time</div>
          <div className="p-2 font-medium">Severity</div>
          <div className="p-2 font-medium">Category</div>
          <div className="p-2 font-medium">Signature</div>
          <div className="p-2 font-medium">Source</div>
          <div className="p-2 font-medium">Destination</div>
        </div>
        {items.length === 0 && !loading ? (
          <div className="text-center text-[var(--text-dim)] py-6">No alerts found for current filters.</div>
        ) : (
          <div style={{ height: rowVirtualizer.getTotalSize(), position: 'relative' }}>
            {rowVirtualizer.getVirtualItems().map((vRow) => {
              const e: any = items[vRow.index]
              return (
                <div
                  key={vRow.key}
                  className={`${GRID_COLS} ${vRow.index % 2 === 0 ? 'bg-[var(--bg-2)]' : 'bg-[var(--panel)]'}`}
                  style={{ position: 'absolute', top: 0, left: 0, right: 0, height: vRow.size, transform: `translateY(${vRow.start}px)` }}
                >
                  <div className="p-2 whitespace-nowrap truncate">{e?.timestamp ? new Date(String(e.timestamp).replace('+0000', '+00:00')).toLocaleString() : '-'}</div>
                  <div className="p-2">
                    {typeof e?.alert?.severity !== 'undefined' ? (
                      e.alert.severity === 1 ? 'High' : e.alert.severity === 2 ? 'Medium' : e.alert.severity === 3 ? 'Low' : String(e.alert.severity)
                    ) : '-'}
                  </div>
                  <div className="p-2 truncate" title={e?.alert?.category || ''}>
                    <button
                      className="underline hover:opacity-80"
                      onClick={() => e?.alert?.category && onCategoryClick(e.alert.category)}
                    >
                      {e?.alert?.category || '-'}
                    </button>
                  </div>
                  <div className="p-2 truncate" title={e?.alert?.signature || ''}>{e?.alert?.signature || '-'}</div>
                  <div className="p-2 truncate" title={`${e?.src_ip || ''}${e?.src_port ? ':' + e.src_port : ''}`}>{e?.src_ip}{e?.src_port ? `:${e.src_port}` : ''}</div>
                  <div className="p-2 truncate" title={`${e?.dest_ip || e?.dst_ip || ''}${e?.dest_port ? ':' + e.dest_port : (e?.dst_port ? ':' + e.dst_port : '')}`}>{e?.dest_ip || e?.dst_ip}{e?.dest_port ? `:${e.dest_port}` : (e?.dst_port ? `:${e.dst_port}` : '')}</div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
