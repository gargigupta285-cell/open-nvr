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

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Layers, RefreshCw, ShieldAlert, Server } from 'lucide-react'
import { apiService } from '../lib/apiService'
import { extractApiError } from '../lib/apiError'
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
