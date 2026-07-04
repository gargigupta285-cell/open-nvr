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

// Read-only registry of AI adapters known to KAI-C: liveness, advertised
// tasks, model identity, and requested permissions (AI Adapter Contract v1).
// Registration, permission approval, and metrics come later with the KAI-C
// /api/v1/adapters migration.

import { useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, Cpu, Database, Globe, HardDrive, Info, Layers, Lock, RefreshCw, ShieldAlert, ShieldCheck, Share2, Server } from 'lucide-react'
import { apiService } from '../lib/apiService'
import { extractApiError } from '../lib/apiError'
import { useSnackbar } from '../components/Snackbar'
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, EmptyState, ErrorCard, PageHeader, Skeleton, type BadgeVariant } from '../components/ui'

type AdapterInfo = Record<string, any>

type HealthResp = { kai_c_status?: string; adapters?: Record<string, AdapterInfo>; message?: string | null }
type CapabilitiesResp = { kai_c?: Record<string, any>; adapters?: Record<string, AdapterInfo> }

function useKaiHealth() {
  return useQuery({
    queryKey: ['kai-c-health'],
    queryFn: async () => {
      const { data } = await apiService.checkKAIHealth()
      return data as HealthResp
    },
    retry: 0,
  })
}

function useKaiCapabilities() {
  return useQuery({
    queryKey: ['kai-c-capabilities'],
    queryFn: async () => {
      const { data } = await apiService.getCapabilities()
      return data as CapabilitiesResp
    },
    retry: 0,
  })
}

function statusVariant(status?: string): BadgeVariant {
  switch ((status || '').toLowerCase()) {
    case 'ok':
    case 'healthy':
    case 'online':
      return 'success'
    case 'degraded':
    case 'loading':
      return 'warning'
    case 'error':
    case 'unhealthy':
    case 'offline':
      return 'destructive'
    default:
      return 'neutral'
  }
}

function asStringList(v: unknown): string[] {
  if (Array.isArray(v)) return v.map(String)
  return []
}

/** Pull the interesting contract fields out of whatever shape the adapter reported. */
function summarizeAdapter(name: string, caps: AdapterInfo | undefined, health: AdapterInfo | undefined) {
  const status = (health?.status ?? health?.health ?? (typeof health === 'string' ? health : undefined)) as string | undefined
  const model = caps?.model ?? {}
  const tasks = asStringList(caps?.tasks_advertised).concat(asStringList(caps?.tasks))
  const permissions = caps?.permissions ?? {}
  const requestedPerms: string[] = []
  if (permissions.gpu) requestedPerms.push('GPU')
  for (const host of asStringList(permissions.network_egress)) requestedPerms.push(`egress: ${host}`)
  for (const path of asStringList(permissions.host_filesystem)) requestedPerms.push(`fs: ${path}`)
  for (const path of asStringList(permissions.shared_memory_paths)) requestedPerms.push(`shm: ${path}`)
  if (permissions.host_metadata) requestedPerms.push('host metadata')
  return {
    name,
    status,
    modelName: model.name ?? caps?.adapter?.name,
    modelVersion: model.version,
    framework: model.framework,
    fingerprint: typeof model.fingerprint === 'string' ? model.fingerprint : undefined,
    tasks: Array.from(new Set(tasks)),
    requestedPerms,
    raw: caps ?? health ?? {},
  }
}

/* ------------------------- Adapter metrics ------------------------- */
// Decision-grade metrics panel (design spec: capabilities-observability §06).
// Each panel is captioned with the operator decision it drives, per the
// "metrics grouped by the decision they drive" table.

type AdapterMetricsResp = {
  adapter: string
  window_s: number
  latency_ms: { p50: number | null; p95: number | null; p99: number | null }
  outcomes: Record<string, number>
  inflight: number | null
  max_inflight: number | null
  queue_depth: number | null
  fingerprint_changes: string[]
  samples: number
}

function formatMs(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—'
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ms`
}

function formatWindow(seconds: number | undefined): string {
  if (!seconds || seconds <= 0) return ''
  if (seconds % 3600 === 0) return `last ${seconds / 3600}h`
  if (seconds % 60 === 0) return `last ${seconds / 60}m`
  return `last ${seconds}s`
}

// ok is green; model errors are the model's fault (amber, tune/rollback);
// provider/transport/refused mean the serving path is broken (red).
function outcomeBarClass(outcome: string): string {
  if (outcome === 'ok') return 'bg-emerald-500'
  if (outcome === 'model_error') return 'bg-amber-500'
  return 'bg-red-500'
}

function MetricPanel({ title, decision, children }: { title: string; decision: string; children: ReactNode }) {
  return (
    <div className="border border-[var(--border)] rounded bg-[var(--bg-2)] p-3">
      <div className="text-[11px] uppercase tracking-wider text-[var(--text-dim)] mb-2 font-mono">{title}</div>
      {children}
      <div className="mt-2 text-[11px] text-[var(--text-dim)]">Decision: {decision}</div>
    </div>
  )
}

function LatencyBars({ latency }: { latency: AdapterMetricsResp['latency_ms'] }) {
  const scale = latency?.p99 ?? 0
  const rows = [
    { label: 'p50', value: latency?.p50 ?? null },
    { label: 'p95', value: latency?.p95 ?? null },
    { label: 'p99', value: latency?.p99 ?? null },
  ]
  return (
    <div className="space-y-1.5">
      {rows.map((r) => {
        const width = r.value != null && scale > 0 ? Math.min(100, Math.max(2, (r.value / scale) * 100)) : 0
        return (
          <div key={r.label} className="grid grid-cols-[32px_1fr_56px] items-center gap-2">
            <span className="font-mono text-xs text-[var(--text-dim)]">{r.label}</span>
            <div className="h-2 rounded bg-[var(--panel-2)] overflow-hidden">
              <div
                className={`h-full rounded ${r.label === 'p99' ? 'bg-amber-500' : 'bg-[var(--accent)]'}`}
                style={{ width: `${width}%` }}
              />
            </div>
            <span className="font-mono text-xs text-right tabular-nums text-[var(--text)]">{formatMs(r.value)}</span>
          </div>
        )
      })}
    </div>
  )
}

function OutcomesSplit({ outcomes }: { outcomes: Record<string, number> }) {
  const entries = Object.entries(outcomes ?? {}).filter(([, n]) => n > 0)
  const total = entries.reduce((sum, [, n]) => sum + n, 0)
  if (total === 0) return <div className="text-xs text-[var(--text-dim)]">No outcomes recorded in this window.</div>
  // ok first, then errors — stable, matches the legend order.
  entries.sort(([a], [b]) => (a === 'ok' ? -1 : b === 'ok' ? 1 : a.localeCompare(b)))
  return (
    <div>
      <div className="flex h-2.5 rounded overflow-hidden mb-2">
        {entries.map(([outcome, n]) => (
          <div key={outcome} className={outcomeBarClass(outcome)} style={{ width: `${(n / total) * 100}%` }} title={`${outcome}: ${n}`} />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {entries.map(([outcome, n]) => (
          <span key={outcome} className="inline-flex items-center gap-1.5 font-mono text-xs text-[var(--text-dim)]">
            <i className={`w-2 h-2 rounded-sm ${outcomeBarClass(outcome)}`} />
            {outcome} {((n / total) * 100).toFixed(total < 200 ? 0 : 1)}%
          </span>
        ))}
      </div>
    </div>
  )
}

function SaturationGauge({ inflight, maxInflight }: { inflight: number; maxInflight: number }) {
  const pct = maxInflight > 0 ? Math.round((inflight / maxInflight) * 100) : null
  const warn = pct != null && pct >= 80
  return (
    <div>
      <div className="flex items-baseline gap-2">
        <span className={`font-mono text-lg font-bold tabular-nums ${warn ? 'text-amber-400' : 'text-[var(--text)]'}`}>
          {inflight} / {maxInflight > 0 ? maxInflight : '—'}
        </span>
        <span className="font-mono text-xs text-[var(--text-dim)]">
          {pct != null ? `${pct}%${warn ? ' — near ceiling' : ''}` : 'no declared ceiling'}
        </span>
      </div>
      <div className="h-2 rounded bg-[var(--panel-2)] overflow-hidden mt-2">
        <div className={`h-full ${warn ? 'bg-amber-500' : 'bg-emerald-500'}`} style={{ width: `${Math.min(100, pct ?? 0)}%` }} />
      </div>
    </div>
  )
}

function FingerprintChanges({ changes }: { changes: string[] }) {
  const count = changes?.length ?? 0
  if (count === 0) return <div className="text-xs text-[var(--text-dim)]">No weight changes observed — fingerprint stable.</div>
  const latest = changes.reduce((a, b) => (a > b ? a : b))
  const latestDate = new Date(latest)
  return (
    <div className="flex items-baseline gap-2">
      <span className="font-mono text-lg font-bold tabular-nums text-[var(--text)]">{count}</span>
      <span className="font-mono text-xs text-[var(--text-dim)]">
        change{count === 1 ? '' : 's'} · latest {Number.isNaN(latestDate.getTime()) ? latest : latestDate.toLocaleString()}
      </span>
    </div>
  )
}

/* ----------------------- Permission approval ---------------------- */
// The visible proof of the governance story (design spec §06): an adapter
// declares the host resources it wants; nothing is granted until an operator
// approves it, and the gateway fails closed until then.

type PermissionKind = 'gpu' | 'network_egress' | 'host_filesystem' | 'shared_memory' | 'host_metadata'

type DeclaredPermission = {
  key: string
  label: string
  kind: PermissionKind
  sovereignty_conflict: boolean
}

type AdapterPermissionsResp = {
  adapter: string
  approval_status: 'pending' | 'approved'
  declared: DeclaredPermission[]
  granted: string[]
  pending: string[]
}

const PERMISSION_KIND_ICON: Record<PermissionKind, ReactNode> = {
  gpu: <Cpu size={13} />,
  network_egress: <Globe size={13} />,
  host_filesystem: <HardDrive size={13} />,
  shared_memory: <Share2 size={13} />,
  host_metadata: <Database size={13} />,
}

function permissionKindIcon(kind: PermissionKind): ReactNode {
  return PERMISSION_KIND_ICON[kind] ?? <Lock size={13} />
}

function useAdapterPermissions(name: string) {
  // The approval badge is important enough to fetch on mount for every card —
  // it's the governance status the operator needs to see immediately.
  return useQuery({
    queryKey: ['adapter-permissions', name],
    queryFn: async () => {
      const { data } = await apiService.getAdapterPermissions(name)
      return data as AdapterPermissionsResp
    },
    retry: 0,
  })
}

function AdapterApprovalBadge({ name }: { name: string }) {
  const query = useAdapterPermissions(name)
  if (query.isPending) return <Skeleton className="h-5 w-28" />
  // Fail loud, not silent: if we can't read approval status, never imply the
  // adapter is fine — surface an explicit unknown state so a pending adapter
  // can't hide behind a failed request.
  if (query.isError) {
    return (
      <Badge variant="neutral">
        <ShieldAlert size={12} /> approval status unavailable
      </Badge>
    )
  }
  const status = query.data?.approval_status
  if (status === 'approved') {
    return (
      <Badge variant="success">
        <ShieldCheck size={12} /> approved
      </Badge>
    )
  }
  if (status === 'pending') {
    return (
      <Badge variant="warning">
        <ShieldAlert size={12} /> approval required
      </Badge>
    )
  }
  return null
}

function AdapterPermissionsSection({ name }: { name: string }) {
  const [open, setOpen] = useState(false)
  const queryClient = useQueryClient()
  const { showSuccess, showError } = useSnackbar()
  const query = useAdapterPermissions(name)

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['adapter-permissions', name] })
    queryClient.invalidateQueries({ queryKey: ['kai-c-capabilities'] })
  }

  const grant = useMutation({
    mutationFn: (keys: string[]) => apiService.grantAdapterPermissions(name, keys),
    onSuccess: (_data, keys) => {
      invalidate()
      showSuccess(`Granted ${keys.length === 1 ? keys[0] : `${keys.length} permissions`} to ${name}.`)
    },
    onError: (err) => showError(extractApiError(err, 'Could not grant permission.')),
  })

  const revoke = useMutation({
    mutationFn: (keys: string[]) => apiService.revokeAdapterPermissions(name, keys),
    onSuccess: (_data, keys) => {
      invalidate()
      showSuccess(`Revoked ${keys.length === 1 ? keys[0] : `${keys.length} permissions`} from ${name}.`)
    },
    onError: (err) => showError(extractApiError(err, 'Could not revoke permission.')),
  })

  const approveAll = useMutation({
    mutationFn: () => apiService.approveAllAdapterPermissions(name),
    onSuccess: () => {
      invalidate()
      showSuccess(`Approved all permissions for ${name}.`)
    },
    onError: (err) => showError(extractApiError(err, 'Could not approve permissions.')),
  })

  const busy = grant.isPending || revoke.isPending || approveAll.isPending
  const p = query.data
  const granted = new Set(p?.granted ?? [])
  const pending = new Set(p?.pending ?? [])
  const anyPending = (p?.pending?.length ?? 0) > 0

  return (
    <div>
      <Button variant="ghost" className="text-xs px-2 py-1" onClick={() => setOpen(!open)}>
        <ShieldCheck size={12} /> {open ? 'Hide permissions' : 'Permissions'}
      </Button>
      {open && (
        <div className="mt-2">
          {query.isPending ? (
            <div className="space-y-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-10" />
              ))}
            </div>
          ) : query.isError ? (
            <div className="text-sm text-red-300/90 border border-red-700/40 rounded p-3">
              {extractApiError(query.error, 'Could not load adapter permissions.')}
            </div>
          ) : p ? (
            <div className="space-y-2">
              {p.approval_status === 'pending' && (
                <div className="flex items-start gap-2 text-xs text-amber-300 border border-amber-700/40 bg-amber-900/20 rounded p-3">
                  <ShieldAlert size={14} className="flex-shrink-0 mt-0.5" />
                  <span>This adapter cannot serve inference until its permissions are approved.</span>
                </div>
              )}

              {p.declared.length === 0 ? (
                <div className="text-xs text-[var(--text-dim)] border border-[var(--border)] rounded bg-[var(--bg-2)] p-3">
                  This adapter declares no host permissions — nothing to approve.
                </div>
              ) : (
                <div className="space-y-1.5">
                  {p.declared.map((perm) => {
                    const isGranted = granted.has(perm.key)
                    const isPending = pending.has(perm.key)
                    return (
                      <div
                        key={perm.key}
                        className="flex items-center gap-2 border border-[var(--border)] rounded bg-[var(--bg-2)] px-3 py-2"
                      >
                        <span className="text-[var(--text-dim)]">{permissionKindIcon(perm.kind)}</span>
                        <div className="min-w-0 flex-1">
                          <div className="text-sm truncate" title={perm.label}>{perm.label}</div>
                          {perm.sovereignty_conflict && (
                            <div className="flex items-center gap-1 text-[11px] text-red-400 mt-0.5">
                              <ShieldAlert size={11} /> conflicts with local_only
                            </div>
                          )}
                        </div>
                        <Badge variant={isGranted ? 'success' : 'warning'}>{isGranted ? 'granted' : 'pending'}</Badge>
                        {isGranted ? (
                          <Button
                            variant="danger"
                            className="text-xs px-2 py-1"
                            disabled={busy}
                            onClick={() => revoke.mutate([perm.key])}
                          >
                            Revoke
                          </Button>
                        ) : (
                          <Button
                            variant="primary"
                            className="text-xs px-2 py-1"
                            disabled={busy || !isPending}
                            onClick={() => grant.mutate([perm.key])}
                          >
                            Grant
                          </Button>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}

              {anyPending && (
                <div className="flex items-center justify-between gap-2 pt-1">
                  <div className="flex items-center gap-1 text-[11px] text-[var(--text-dim)]">
                    <Info size={11} /> {p.pending.length} permission{p.pending.length === 1 ? '' : 's'} awaiting approval
                  </div>
                  <Button variant="primary" className="text-xs px-2 py-1" disabled={busy} onClick={() => approveAll.mutate()}>
                    <ShieldCheck size={12} /> Approve all
                  </Button>
                </div>
              )}
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}

function AdapterMetricsSection({ name }: { name: string }) {
  const [open, setOpen] = useState(false)
  const query = useQuery({
    queryKey: ['adapter-metrics', name],
    queryFn: async () => {
      const { data } = await apiService.getAdapterMetrics(name)
      return data as AdapterMetricsResp
    },
    enabled: open, // lazy: only fetch once the operator opens the panel
    retry: 0,
  })

  const m = query.data
  const noSamples = m != null && (m.samples ?? 0) === 0

  return (
    <div>
      <div className="flex items-center gap-1">
        <Button variant="ghost" className="text-xs px-2 py-1" onClick={() => setOpen(!open)}>
          <Activity size={12} /> {open ? 'Hide metrics' : 'Metrics'}
        </Button>
        {open && !query.isPending && (
          <Button variant="ghost" className="text-xs px-2 py-1" onClick={() => query.refetch()} disabled={query.isFetching}>
            <RefreshCw size={12} className={query.isFetching ? 'animate-spin' : ''} />
          </Button>
        )}
      </div>
      {open && (
        <div className="mt-2">
          {query.isPending ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-24" />
              ))}
            </div>
          ) : query.isError ? (
            <div className="text-sm text-red-300/90 border border-red-700/40 rounded p-3">
              {extractApiError(query.error, 'Could not load adapter metrics.')}
            </div>
          ) : noSamples ? (
            <div className="text-xs text-[var(--text-dim)] border border-[var(--border)] rounded bg-[var(--bg-2)] p-3">
              No samples yet — this adapter hasn't served governed inference in the {formatWindow(m?.window_s) || 'current'} window.
            </div>
          ) : m ? (
            <div className="space-y-2">
              <div className="font-mono text-[11px] text-[var(--text-dim)]">
                {formatWindow(m.window_s)}{m.samples != null ? ` · ${m.samples} samples` : ''}
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <MetricPanel title="Inference latency" decision="which model per camera; is the SLA breached">
                  <LatencyBars latency={m.latency_ms} />
                </MetricPanel>
                <MetricPanel title="Outcomes" decision="rollback / retire / investigate the adapter">
                  <OutcomesSplit outcomes={m.outcomes} />
                </MetricPanel>
                <MetricPanel title="Saturation" decision="scale out a replica / rebalance cameras">
                  <SaturationGauge inflight={m.inflight ?? 0} maxInflight={m.max_inflight ?? 0} />
                </MetricPanel>
                <MetricPanel title="Queue depth" decision="throttle fan-in / drop to keyframes">
                  <div className="flex items-baseline gap-2">
                    <span className={`font-mono text-lg font-bold tabular-nums ${(m.queue_depth ?? 0) > 0 ? 'text-amber-400' : 'text-[var(--text)]'}`}>
                      {m.queue_depth ?? 0}
                    </span>
                    <span className="font-mono text-xs text-[var(--text-dim)]">frames waiting on the model</span>
                  </div>
                </MetricPanel>
                <MetricPanel title="Fingerprint / drift" decision="re-validate accuracy; freeze for compliance">
                  <FingerprintChanges changes={m.fingerprint_changes ?? []} />
                </MetricPanel>
              </div>
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}

export function AIAdapters() {
  const healthQuery = useKaiHealth()
  const capsQuery = useKaiCapabilities()
  const [expanded, setExpanded] = useState<string | null>(null)

  const loading = healthQuery.isPending || capsQuery.isPending
  const bothFailed = healthQuery.isError && capsQuery.isError

  const adapterNames = new Set<string>([
    ...Object.keys(capsQuery.data?.adapters ?? {}),
    ...Object.keys(healthQuery.data?.adapters ?? {}),
  ])
  const adapters = Array.from(adapterNames)
    .sort()
    .map((name) => summarizeAdapter(name, capsQuery.data?.adapters?.[name], healthQuery.data?.adapters?.[name]))

  const refresh = () => {
    healthQuery.refetch()
    capsQuery.refetch()
  }

  return (
    <section className="space-y-4">
      <PageHeader
        title="AI Adapters"
        description="Models registered with KAI-C, the sovereignty and audit gateway. Every inference the platform runs goes through one of these adapters."
        actions={
          <Button onClick={refresh} disabled={loading}>
            <RefreshCw size={14} className={healthQuery.isFetching || capsQuery.isFetching ? 'animate-spin' : ''} /> Refresh
          </Button>
        }
      />

      {/* KAI-C gateway status */}
      <Card>
        <CardHeader>
          <Server size={16} className="text-[var(--text-dim)]" />
          <CardTitle>KAI-C Gateway</CardTitle>
          <div className="ml-auto">
            {healthQuery.isPending ? (
              <Skeleton className="h-5 w-16" />
            ) : (
              <Badge variant={statusVariant(healthQuery.data?.kai_c_status ?? (healthQuery.isError ? 'error' : undefined))}>
                {healthQuery.data?.kai_c_status ?? (healthQuery.isError ? 'unreachable' : 'unknown')}
              </Badge>
            )}
          </div>
        </CardHeader>
        {(healthQuery.data?.message || healthQuery.isError) && (
          <CardContent>
            <div className="text-sm text-[var(--text-dim)]">
              {healthQuery.data?.message ?? extractApiError(healthQuery.error, 'KAI-C is not reachable from the backend.')}
            </div>
          </CardContent>
        )}
      </Card>

      {/* Adapters */}
      {loading ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-40" />
          ))}
        </div>
      ) : bothFailed ? (
        <ErrorCard
          title="Adapter registry unavailable"
          message={extractApiError(capsQuery.error, 'Could not load adapter capabilities from KAI-C.')}
          onRetry={refresh}
        />
      ) : adapters.length === 0 ? (
        <EmptyState
          icon={<Layers size={28} />}
          title="No adapters registered"
          description="Start an AI adapter (YOLOv8, BLIP, Whisper, …) and register it with KAI-C to see it here. See docs/AI_ADAPTER_CONTRACT.md for the contract."
        />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {adapters.map((a) => (
            <Card key={a.name}>
              <CardHeader>
                <Layers size={16} className="text-[var(--text-dim)]" />
                <CardTitle>{a.name}</CardTitle>
                <div className="ml-auto flex items-center gap-2">
                  <AdapterApprovalBadge name={a.name} />
                  {a.status && <Badge variant={statusVariant(a.status)}>{a.status}</Badge>}
                </div>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                  <div className="text-[var(--text-dim)]">Model</div>
                  <div>{a.modelName ?? '—'}{a.modelVersion ? ` · v${a.modelVersion}` : ''}</div>
                  {a.framework && (
                    <>
                      <div className="text-[var(--text-dim)]">Framework</div>
                      <div>{a.framework}</div>
                    </>
                  )}
                  {a.fingerprint && (
                    <>
                      <div className="text-[var(--text-dim)]">Fingerprint</div>
                      <div className="font-mono text-xs truncate" title={a.fingerprint}>{a.fingerprint}</div>
                    </>
                  )}
                </div>

                {a.tasks.length > 0 && (
                  <div>
                    <div className="text-[var(--text-dim)] mb-1">Tasks</div>
                    <div className="flex flex-wrap gap-1">
                      {a.tasks.map((t) => (
                        <Badge key={t} variant="info">{t}</Badge>
                      ))}
                    </div>
                  </div>
                )}

                {a.requestedPerms.length > 0 && (
                  <div>
                    <div className="text-[var(--text-dim)] mb-1 flex items-center gap-1">
                      <ShieldAlert size={12} /> Requested permissions
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {a.requestedPerms.map((p) => (
                        <Badge key={p} variant="warning">{p}</Badge>
                      ))}
                    </div>
                  </div>
                )}

                <AdapterPermissionsSection name={a.name} />

                <AdapterMetricsSection name={a.name} />

                <div>
                  <Button variant="ghost" className="text-xs px-2 py-1" onClick={() => setExpanded(expanded === a.name ? null : a.name)}>
                    {expanded === a.name ? 'Hide raw capabilities' : 'Show raw capabilities'}
                  </Button>
                  {expanded === a.name && (
                    <pre className="mt-2 p-3 text-xs leading-snug overflow-auto max-h-64 border border-[var(--border)] rounded bg-[var(--bg-2)]">
                      {JSON.stringify(a.raw, null, 2)}
                    </pre>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </section>
  )
}
