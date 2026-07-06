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

// App Catalog: detector apps built on the OpenNVR App SDK. Cards read from
// GET /api/v1/apps; each checks its manifest.requires_tasks against the tasks
// advertised by KAI-C adapters, so the operator can see whether the backing
// model is present before enabling. Config forms are generated from the
// manifest param schema — no app-specific UI code.

import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, Boxes, Check, Copy, Download, ExternalLink, RefreshCw, Settings2, Trash2 } from 'lucide-react'
import { apiService } from '../lib/apiService'
import { extractApiError } from '../lib/apiError'
import { Modal } from '../components/Modal'
import { useSnackbar } from '../components/Snackbar'
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, EmptyState, ErrorCard, PageHeader, Skeleton, type BadgeVariant } from '../components/ui'

type ManifestParam = {
  name: string
  type: string
  default?: any
  per_camera?: boolean
  required?: boolean
  description?: string
}

type AppManifest = {
  id: string
  name: string
  version: string
  category: string
  summary?: string
  requires_tasks?: string[]
  subscribes?: string[]
  params?: ManifestParam[]
  emits?: { name: string; severity?: string; description?: string }[]
  // Declarative live-state views (SDK StateView.to_dict()) — rendered
  // generically by LiveStateViews below; absent for older manifests.
  state_schema?: {
    name: string
    label: string
    kind: 'metric' | 'table'
    path?: string
    columns?: string[]
    description?: string
  }[]
}

type RegisteredApp = {
  id: string
  name: string
  category: string
  version: string
  url: string
  enabled: boolean
  status?: string
  last_seen?: string | null
  manifest?: AppManifest | null
  config?: Record<string, any> | null
}

type AppStatusResp = {
  health?: { status?: string; [k: string]: any } | null
  state?: any
}

// An entry from GET /api/v1/apps/index — the store listing. Entries with
// installed=true are already registered and surface under "Installed" instead.
type IndexApp = {
  id: string
  name: string
  summary?: string
  category: string
  version: string
  image?: string
  requires_tasks?: string[]
  emits?: string[]
  docs_url?: string
  install?: { compose?: string; command?: string }
  installed: boolean
  enabled: boolean | null
}

type CapabilitiesResp = { kai_c?: Record<string, any>; adapters?: Record<string, Record<string, any>> }

// Reconciler-driven install lifecycle. install/uninstall POST an intent that
// lands "pending"; the reconciler later flips it to "applied" or "failed".
type InstallStatusPhase = 'none' | 'pending' | 'applied' | 'failed'
type InstallStatusResp = {
  id: string // the app id (the server serializes `id`, not `app_id`)
  status: InstallStatusPhase
  message?: string
  image?: string
  image_digest?: string
}

// Stop polling after ~5 minutes of "pending": the reconciler is a
// separately-deployed OPT-IN component, so an intent can legitimately sit
// pending forever (installer container not running) — without a cap the UI
// polls every 2s per app per tab indefinitely with the buttons stuck on
// "Installing…". After the cap we stop and surface a hint instead.
const INSTALL_POLL_MAX_MS = 5 * 60 * 1000

// The install/uninstall endpoints are opt-in + RBAC gated: a 403 is a normal,
// expected path (operator disabled one-click, or caller lacks apps.install) —
// NOT an error to toast. Callers treat it as "fall back to the command."
function isForbidden(e: any): boolean {
  return e?.status === 403 || e?.response?.status === 403
}

function useApps() {
  return useQuery({
    queryKey: ['apps'],
    queryFn: async () => {
      const { data } = await apiService.getApps()
      return (Array.isArray(data) ? data : []) as RegisteredApp[]
    },
    retry: 0,
  })
}

// The index is best-effort: if the endpoint 404s or errors, the Installed group
// still renders. retry:0 keeps a missing endpoint from thrashing.
function useAppIndex() {
  return useQuery({
    queryKey: ['apps-index'],
    queryFn: async () => {
      const { data } = await apiService.getAppIndex()
      const apps = (data as { apps?: unknown })?.apps
      return (Array.isArray(apps) ? apps : []) as IndexApp[]
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

// Polls install-status while a reconcile is in flight. Enabled only after an
// install/uninstall intent is accepted (`active`); refetches on an interval
// while pending and stops once the reconciler reports applied/failed — or
// once the poll budget is spent (the installer may simply not be running).
// On applied it invalidates ['apps'] + ['apps-index'] so the app hops groups.
function useInstallStatusPoll(id: string, active: boolean) {
  const queryClient = useQueryClient()
  const startedAtRef = useRef<number | null>(null)
  const [timedOut, setTimedOut] = useState(false)
  useEffect(() => {
    // Reset the budget each time a new intent starts polling.
    startedAtRef.current = active ? Date.now() : null
    setTimedOut(false)
  }, [active, id])
  const query = useQuery({
    queryKey: ['app-install-status', id],
    queryFn: async () => {
      const { data } = await apiService.getInstallStatus(id)
      return data as InstallStatusResp
    },
    enabled: active && !timedOut,
    retry: 0,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (status === 'applied') {
        queryClient.invalidateQueries({ queryKey: ['apps'] })
        queryClient.invalidateQueries({ queryKey: ['apps-index'] })
        return false
      }
      if (status !== 'pending') return false
      const started = startedAtRef.current
      if (started !== null && Date.now() - started > INSTALL_POLL_MAX_MS) {
        setTimedOut(true)
        return false
      }
      return 2000
    },
  })
  return { ...query, timedOut }
}

function InstallStatusNote({ status }: { status: InstallStatusResp }) {
  const variant: BadgeVariant =
    status.status === 'applied' ? 'success' : status.status === 'failed' ? 'destructive' : 'warning'
  const label =
    status.status === 'applied'
      ? 'applied'
      : status.status === 'failed'
        ? 'failed'
        : status.status === 'pending'
          ? 'pending'
          : status.status
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <Badge variant={variant}>{label}</Badge>
      {status.message && <span className="text-[var(--text-dim)]">{status.message}</span>}
      {status.image && <code className="text-xs text-[var(--text-dim)]">{status.image}</code>}
    </div>
  )
}

function asStringList(v: unknown): string[] {
  if (Array.isArray(v)) return v.map(String)
  return []
}

/** Union of every task advertised by any adapter registered with KAI-C. */
function availableTasks(caps: CapabilitiesResp | undefined): Set<string> {
  const tasks = new Set<string>()
  for (const info of Object.values(caps?.adapters ?? {})) {
    for (const t of asStringList(info?.tasks_advertised).concat(asStringList(info?.tasks))) tasks.add(t)
  }
  return tasks
}

function statusVariant(status?: string): BadgeVariant {
  switch ((status || '').toLowerCase()) {
    case 'ok':
    case 'ready':
    case 'running':
    case 'healthy':
    case 'online':
      return 'success'
    case 'degraded':
    case 'starting':
    case 'loading':
      return 'warning'
    case 'error':
    case 'unhealthy':
    case 'unreachable':
    case 'offline':
      return 'destructive'
    default:
      return 'neutral'
  }
}

/** Params whose values aren't scalar edit as JSON in the generated form. */
function isJsonParam(p: ManifestParam): boolean {
  const t = (p.type || '').toLowerCase()
  return p.per_camera === true || t === 'list' || t.startsWith('geometry.') || t === 'dict' || t === 'json'
}

function initialFormValue(p: ManifestParam, config: Record<string, any> | null | undefined): string | boolean {
  const current = config && p.name in config ? config[p.name] : p.default
  if (p.type === 'bool' && !isJsonParam(p)) return Boolean(current)
  if (isJsonParam(p)) return current === undefined ? '' : JSON.stringify(current, null, 2)
  return current === undefined || current === null ? '' : String(current)
}

/* ------------------------- Config modal ------------------------- */

function AppConfigModal({ app, onClose }: { app: RegisteredApp; onClose: () => void }) {
  const queryClient = useQueryClient()
  const { showSuccess } = useSnackbar()
  const params = app.manifest?.params ?? []
  const [values, setValues] = useState<Record<string, string | boolean>>(() =>
    Object.fromEntries(params.map((p) => [p.name, initialFormValue(p, app.config)]))
  )
  const [error, setError] = useState<string | null>(null)

  const saveMutation = useMutation({
    mutationFn: (config: Record<string, any>) => apiService.updateAppConfig(app.id, config),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['apps'] })
      showSuccess(`${app.name} configuration saved`)
      onClose()
    },
    onError: (e) => setError(extractApiError(e, 'Failed to save app configuration.')),
  })

  const submit = () => {
    setError(null)
    const config: Record<string, any> = {}
    for (const p of params) {
      const raw = values[p.name]
      const t = (p.type || '').toLowerCase()
      if (isJsonParam(p)) {
        const text = String(raw ?? '').trim()
        if (!text) {
          if (p.required) {
            setError(`"${p.name}" is required.`)
            return
          }
          continue
        }
        try {
          config[p.name] = JSON.parse(text)
        } catch {
          setError(`"${p.name}" is not valid JSON.`)
          return
        }
      } else if (t === 'bool') {
        config[p.name] = Boolean(raw)
      } else if (t === 'int' || t === 'float') {
        const text = String(raw ?? '').trim()
        if (!text) {
          if (p.required) {
            setError(`"${p.name}" is required.`)
            return
          }
          continue
        }
        const num = Number(text)
        if (!Number.isFinite(num) || (t === 'int' && !Number.isInteger(num))) {
          setError(`"${p.name}" must be a valid ${t === 'int' ? 'integer' : 'number'}.`)
          return
        }
        config[p.name] = num
      } else {
        const text = String(raw ?? '')
        if (!text && p.required) {
          setError(`"${p.name}" is required.`)
          return
        }
        if (text || !p.required) config[p.name] = text
      }
    }
    saveMutation.mutate(config)
  }

  return (
    <Modal open title={`Configure ${app.name}`} onClose={onClose} widthClassName="w-[560px]">
      {params.length === 0 ? (
        <div className="text-sm text-[var(--text-dim)]">This app declares no configurable parameters.</div>
      ) : (
        <div className="space-y-4">
          {params.map((p) => {
            const t = (p.type || '').toLowerCase()
            const value = values[p.name]
            return (
              <div key={p.name}>
                <label className="block text-sm mb-1">
                  <span className="font-medium">{p.name}</span>
                  <span className="ml-2 text-xs text-[var(--text-dim)]">
                    {p.type}
                    {p.per_camera ? ' · per camera' : ''}
                    {p.required ? ' · required' : ''}
                  </span>
                </label>
                {p.description && <div className="text-xs text-[var(--text-dim)] mb-1">{p.description}</div>}
                {isJsonParam(p) ? (
                  <textarea
                    className="w-full h-28 px-2 py-1.5 text-sm font-mono rounded border border-[var(--border)] bg-[var(--bg-2)] text-[var(--text)]"
                    value={String(value ?? '')}
                    placeholder={p.per_camera ? '{"camera_id": …}' : '[…]'}
                    onChange={(e) => setValues((v) => ({ ...v, [p.name]: e.target.value }))}
                  />
                ) : t === 'bool' ? (
                  <label className="inline-flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={Boolean(value)}
                      onChange={(e) => setValues((v) => ({ ...v, [p.name]: e.target.checked }))}
                    />
                    Enabled
                  </label>
                ) : (
                  <input
                    type={t === 'int' || t === 'float' ? 'number' : 'text'}
                    step={t === 'float' ? 'any' : undefined}
                    className="w-full px-2 py-1.5 text-sm rounded border border-[var(--border)] bg-[var(--bg-2)] text-[var(--text)]"
                    value={String(value ?? '')}
                    onChange={(e) => setValues((v) => ({ ...v, [p.name]: e.target.value }))}
                  />
                )}
              </div>
            )
          })}
        </div>
      )}

      {error && <div className="mt-3 text-sm text-red-400">{error}</div>}

      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        {params.length > 0 && (
          <Button variant="primary" onClick={submit} disabled={saveMutation.isPending}>
            {saveMutation.isPending ? 'Saving…' : 'Save'}
          </Button>
        )}
      </div>
    </Modal>
  )
}

/* -------------------------- Status chip -------------------------- */

// Lazy: only fetched after the operator clicks "Check" — the registry list is
// cheap, but /status proxies to each app's /health, so don't fan out on mount.
function AppStatusChip({ appId }: { appId: string }) {
  const [requested, setRequested] = useState(false)
  const statusQuery = useQuery({
    queryKey: ['app-status', appId],
    queryFn: async () => {
      const { data } = await apiService.getAppStatus(appId)
      return data as AppStatusResp
    },
    enabled: requested,
    retry: 0,
  })

  if (!requested) {
    return (
      <Button variant="ghost" className="text-xs px-2 py-1" onClick={() => setRequested(true)}>
        <Activity size={12} /> Check
      </Button>
    )
  }
  if (statusQuery.isPending) return <Skeleton className="h-5 w-16" />
  if (statusQuery.isError) {
    return <Badge variant="destructive">{extractApiError(statusQuery.error, 'status check failed')}</Badge>
  }
  const health = statusQuery.data?.health?.status ?? 'unknown'
  return (
    <span className="inline-flex items-center gap-1">
      <Badge variant={statusVariant(health)}>{health}</Badge>
      <Button variant="ghost" className="text-xs px-1.5 py-1" onClick={() => statusQuery.refetch()} title="Re-check status">
        <RefreshCw size={12} className={statusQuery.isFetching ? 'animate-spin' : ''} />
      </Button>
    </span>
  )
}

/* ----------------------- Declarative state views ------------------ */

// Manifest-declared views over GET /state (SDK StateView) — the same
// zero-app-specific-UI bet as params → config form. Rendered from the
// SAME react-query cache entry the status chip fills, so the live state
// appears when the operator clicks "Check" and refreshes with it.
type StateViewSpec = {
  name: string
  label: string
  kind: 'metric' | 'table'
  path?: string
  columns?: string[]
  description?: string
}

function getStatePath(obj: any, path?: string): any {
  if (!path) return obj
  return path.split('.').reduce((acc: any, key: string) => (acc == null ? undefined : acc[key]), obj)
}

function MetricView({ view, value }: { view: StateViewSpec; value: any }) {
  let display: string
  if (value == null) display = '—'
  else if (Array.isArray(value)) display = String(value.length)
  else if (typeof value === 'object') display = String(Object.keys(value).length)
  else display = String(value)
  return (
    <div
      className="rounded border border-[var(--border)] bg-[var(--bg-2)] px-2 py-1 text-xs"
      title={view.description || view.name}
    >
      <span className="text-[var(--text-dim)]">{view.label}</span>{' '}
      <span className="font-semibold">{display}</span>
    </div>
  )
}

function TableView({ view, value }: { view: StateViewSpec; value: any }) {
  // Accept a list of dicts, a list of scalars, or a dict-of-dicts
  // (rendered with the key as a leading "id" column) — /state shapes
  // vary and the renderer must never crash on live data.
  let rows: Record<string, any>[] = []
  if (Array.isArray(value)) {
    rows = value.map((v) => (v != null && typeof v === 'object' ? v : { value: v }))
  } else if (value != null && typeof value === 'object') {
    rows = Object.entries(value).map(([k, v]) => ({
      id: k,
      ...(v != null && typeof v === 'object' ? (v as Record<string, any>) : { value: v }),
    }))
  }
  if (rows.length === 0) return null
  const columns =
    view.columns && view.columns.length > 0
      ? view.columns.filter((c) => rows.some((r) => c in r))
      : Array.from(new Set(rows.flatMap((r) => Object.keys(r)))).slice(0, 6)
  if (columns.length === 0) return null
  return (
    <div className="overflow-x-auto" title={view.description || view.name}>
      <div className="text-xs text-[var(--text-dim)] mb-1">{view.label}</div>
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c} className="text-left px-2 py-1 border-b border-[var(--border)] text-[var(--text-dim)] font-normal">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 25).map((row, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c} className="px-2 py-1 border-b border-[var(--border)]">
                  {row[c] == null ? '—' : typeof row[c] === 'object' ? JSON.stringify(row[c]) : String(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 25 && (
        <div className="text-xs text-[var(--text-dim)] mt-1">…and {rows.length - 25} more</div>
      )}
    </div>
  )
}

function LiveStateViews({ appId, views }: { appId: string; views: StateViewSpec[] }) {
  // Same key as AppStatusChip; enabled:false — this component only
  // OBSERVES the cache the chip's "Check" fills (no extra fan-out).
  const statusQuery = useQuery({
    queryKey: ['app-status', appId],
    queryFn: async () => {
      const { data } = await apiService.getAppStatus(appId)
      return data as AppStatusResp
    },
    enabled: false,
    retry: 0,
  })
  const state = statusQuery.data?.state
  if (state == null) return null
  const metrics = views.filter((v) => v.kind === 'metric')
  const tables = views.filter((v) => v.kind === 'table')
  return (
    <div className="space-y-2 pt-1">
      {metrics.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {metrics.map((v) => (
            <MetricView key={v.name} view={v} value={getStatePath(state, v.path)} />
          ))}
        </div>
      )}
      {tables.map((v) => (
        <TableView key={v.name} view={v} value={getStatePath(state, v.path)} />
      ))}
    </div>
  )
}

/* ---------------------------- App card --------------------------- */

function AppCard({ app, tasks, onConfigure }: { app: RegisteredApp; tasks: Set<string>; onConfigure: () => void }) {
  const queryClient = useQueryClient()
  const { showSuccess, showError } = useSnackbar()
  const [uninstallPollActive, setUninstallPollActive] = useState(false)
  const [uninstallNote, setUninstallNote] = useState<string | null>(null)

  const requires = asStringList(app.manifest?.requires_tasks)
  const missing = requires.filter((t) => !tasks.has(t))

  const toggleMutation = useMutation({
    mutationFn: () => (app.enabled ? apiService.disableApp(app.id) : apiService.enableApp(app.id)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['apps'] })
      showSuccess(`${app.name} ${app.enabled ? 'disabled' : 'enabled'}`)
    },
    onError: (e) => showError(extractApiError(e, `Failed to ${app.enabled ? 'disable' : 'enable'} ${app.name}.`)),
  })

  const uninstallStatus = useInstallStatusPoll(app.id, uninstallPollActive)

  // Same opt-in + RBAC gating as install: a 403 degrades to an inline note
  // (fail-closed) rather than an error toast, since the operator may have
  // one-click disabled and expects to run the removal command by hand.
  const uninstallMutation = useMutation({
    mutationFn: () => apiService.uninstallApp(app.id),
    onMutate: () => setUninstallNote(null),
    onSuccess: () => {
      showSuccess(`Uninstall requested — ${app.name} will be removed once the reconciler applies it`)
      setUninstallPollActive(true)
    },
    onError: (e) => {
      if (isForbidden(e)) {
        setUninstallNote(
          extractApiError(
            e,
            "One-click uninstall is disabled by your operator (or you don't have permission)."
          )
        )
      } else {
        setUninstallNote(extractApiError(e, `Failed to request uninstall of ${app.name}.`))
      }
    },
  })

  // timedOut releases the button (installer may not be running) — same
  // anti-wedge as the install modal.
  const uninstallInFlight =
    uninstallMutation.isPending ||
    (uninstallStatus.data?.status === 'pending' && !uninstallStatus.timedOut)

  const confirmUninstall = () => {
    if (window.confirm(`Uninstall ${app.name}? This asks the reconciler to remove the app from this host.`)) {
      uninstallMutation.mutate()
    }
  }

  return (
    <Card>
      <CardHeader>
        <Boxes size={16} className="text-[var(--text-dim)]" />
        <CardTitle>{app.name}</CardTitle>
        <Badge variant="info">{app.category}</Badge>
        <span className="text-xs text-[var(--text-dim)]">v{app.version}</span>
        <div className="ml-auto">
          <AppStatusChip appId={app.id} />
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="text-[var(--text-dim)]">{app.manifest?.summary || 'No summary provided.'}</div>

        {requires.length > 0 && (
          <div>
            {missing.length === 0 ? (
              <Badge variant="success">● requires {requires.join(' + ')} — available</Badge>
            ) : (
              <Badge variant="warning">requires {missing.join(' + ')} — not installed</Badge>
            )}
          </div>
        )}

        {Array.isArray(app.manifest?.state_schema) && app.manifest.state_schema.length > 0 && (
          <LiveStateViews appId={app.id} views={app.manifest.state_schema as StateViewSpec[]} />
        )}

        <div className="flex items-center gap-2 pt-1">
          <Button
            variant={app.enabled ? 'default' : 'primary'}
            onClick={() => toggleMutation.mutate()}
            disabled={toggleMutation.isPending}
            aria-pressed={app.enabled}
          >
            {toggleMutation.isPending ? 'Working…' : app.enabled ? 'Disable' : 'Enable'}
          </Button>
          <Button variant="outline" onClick={onConfigure}>
            <Settings2 size={14} /> Configure
          </Button>
          <Button variant="danger" onClick={confirmUninstall} disabled={uninstallInFlight}>
            <Trash2 size={14} /> {uninstallInFlight ? 'Uninstalling…' : 'Uninstall'}
          </Button>
          <Badge variant={app.enabled ? 'success' : 'neutral'} className="ml-auto">
            {app.enabled ? 'enabled' : 'disabled'}
          </Badge>
        </div>

        {uninstallNote && <div className="text-sm text-amber-400">{uninstallNote}</div>}
        {uninstallPollActive && uninstallStatus.data && <InstallStatusNote status={uninstallStatus.data} />}
        {uninstallStatus.timedOut && (
          <div className="text-xs text-amber-400">
            Still pending after 5 minutes — the app-installer service may not be
            running (see docs/APPS_INSTALL.md).
          </div>
        )}
      </CardContent>
    </Card>
  )
}

/* -------------------------- Install modal ------------------------ */

// The command/compose display is the ALWAYS-PRESENT fallback (the fail-closed
// path): install may be disabled or unpermitted, so we never assume one-click
// works. When the backend opts in AND the caller is permitted, the primary
// "Install (one-click)" button POSTs an intent and we poll the reconciler; a
// 403 quietly degrades to "run the command below instead."
function InstallModal({ app, onClose }: { app: IndexApp; onClose: () => void }) {
  const { showSuccess, showError } = useSnackbar()
  const [copied, setCopied] = useState<string | null>(null)
  const [pollActive, setPollActive] = useState(false)
  const [forbidden, setForbidden] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const statusQuery = useInstallStatusPoll(app.id, pollActive)

  const installMutation = useMutation({
    mutationFn: () => apiService.installApp(app.id),
    onMutate: () => {
      setForbidden(null)
      setError(null)
    },
    onSuccess: () => {
      showSuccess('Install requested — the app will appear under Installed once the reconciler applies it')
      setPollActive(true)
    },
    onError: (e) => {
      if (isForbidden(e)) {
        setForbidden(
          extractApiError(
            e,
            "One-click install is disabled by your operator (or you don't have permission). Run the command below instead."
          )
        )
      } else {
        setError(extractApiError(e, 'Failed to request install.'))
      }
    },
  })

  const status = statusQuery.data?.status
  // timedOut releases the button: an intent can sit "pending" forever when
  // the opt-in installer container isn't running — don't wedge the modal.
  const inFlight =
    installMutation.isPending || (status === 'pending' && !statusQuery.timedOut)

  const copy = async (label: string, text: string) => {
    try {
      // navigator.clipboard is unavailable in non-secure (plain-HTTP) or
      // sandboxed contexts — fall back to execCommand so an operator on a
      // LAN http:// deployment can still copy the install command.
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text)
      } else {
        const ta = document.createElement('textarea')
        ta.value = text
        ta.style.position = 'fixed'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select()
        const ok = document.execCommand('copy')
        document.body.removeChild(ta)
        if (!ok) throw new Error('copy command rejected')
      }
      setCopied(label)
      showSuccess('Copied to clipboard')
      window.setTimeout(() => setCopied((c) => (c === label ? null : c)), 1500)
    } catch {
      showError('Copy failed — select the text and copy manually')
    }
  }

  const command = app.install?.command?.trim()
  const compose = app.install?.compose?.trim()

  return (
    <Modal open title={`Install ${app.name}`} onClose={onClose} widthClassName="w-[640px]">
      <div className="space-y-4">
        {/* Primary path: one-click install (opt-in + RBAC gated server-side). */}
        <div className="rounded border border-[var(--border)] bg-[var(--bg-2)] p-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" onClick={() => installMutation.mutate()} disabled={inFlight}>
              <Download size={14} /> {inFlight ? 'Installing…' : 'Install (one-click)'}
            </Button>
            <span className="text-xs text-[var(--text-dim)]">
              The backend enforces opt-in and permissions; if it's disabled, use the command below.
            </span>
          </div>
          {forbidden && <div className="text-sm text-amber-400">{forbidden}</div>}
          {error && <div className="text-sm text-red-400">{error}</div>}
          {pollActive && statusQuery.data && <InstallStatusNote status={statusQuery.data} />}
          {statusQuery.timedOut && (
            <div className="text-sm text-amber-400">
              Still pending after 5 minutes — the app-installer service may not be
              running. Start it with the app-installer compose profile (see
              docs/APPS_INSTALL.md), or use the command below.
            </div>
          )}
        </div>

        <div className="text-sm text-[var(--text-dim)]">
          Or run this on your OpenNVR host, then the app self-registers and appears under Installed.
        </div>

        {command && (
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-medium text-[var(--text-dim)]">Command</span>
              <Button variant="ghost" className="text-xs px-2 py-1" onClick={() => copy('command', command)}>
                {copied === 'command' ? <Check size={12} /> : <Copy size={12} />} Copy
              </Button>
            </div>
            <pre className="w-full overflow-x-auto rounded border border-[var(--border)] bg-[var(--bg-2)] p-3 text-xs text-[var(--text)] font-mono whitespace-pre-wrap">
              {command}
            </pre>
          </div>
        )}

        {compose && (
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-medium text-[var(--text-dim)]">docker-compose.yml</span>
              <Button variant="ghost" className="text-xs px-2 py-1" onClick={() => copy('compose', compose)}>
                {copied === 'compose' ? <Check size={12} /> : <Copy size={12} />} Copy
              </Button>
            </div>
            <pre className="w-full max-h-72 overflow-auto rounded border border-[var(--border)] bg-[var(--bg-2)] p-3 text-xs text-[var(--text)] font-mono">
              {compose}
            </pre>
          </div>
        )}

        {!command && !compose && (
          <div className="text-sm text-[var(--text-dim)]">This entry provides no install instructions.</div>
        )}
      </div>

      <div className="mt-4 flex justify-end">
        <Button variant="primary" onClick={onClose}>Done</Button>
      </div>
    </Modal>
  )
}

/* ------------------------ Available app card --------------------- */

function AvailableAppCard({ app, tasks, onInstall }: { app: IndexApp; tasks: Set<string>; onInstall: () => void }) {
  const requires = asStringList(app.requires_tasks)
  const missing = requires.filter((t) => !tasks.has(t))

  return (
    <Card>
      <CardHeader>
        <Boxes size={16} className="text-[var(--text-dim)]" />
        <CardTitle>{app.name}</CardTitle>
        <Badge variant="info">{app.category}</Badge>
        <span className="text-xs text-[var(--text-dim)]">v{app.version}</span>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="text-[var(--text-dim)]">{app.summary || 'No summary provided.'}</div>

        {requires.length > 0 && (
          <div>
            {missing.length === 0 ? (
              <Badge variant="success">● requires {requires.join(' + ')} — available</Badge>
            ) : (
              <Badge variant="warning">requires {missing.join(' + ')} — not installed</Badge>
            )}
          </div>
        )}

        <div className="flex items-center gap-2 pt-1">
          <Button variant="primary" onClick={onInstall}>
            <Download size={14} /> Install
          </Button>
          {app.docs_url && (
            <a
              href={app.docs_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-sm text-[var(--text-dim)] hover:text-[var(--text)]"
            >
              <ExternalLink size={14} /> Docs
            </a>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

/* ----------------------------- View ------------------------------ */

function SkeletonGrid({ count = 3 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3">
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton key={i} className="h-44" />
      ))}
    </div>
  )
}

function GroupHeader({ title, count }: { title: string; count?: number }) {
  return (
    <h3 className="text-sm font-semibold text-[var(--text)] tracking-wide">
      {title}
      {typeof count === 'number' && count > 0 && (
        <span className="ml-2 text-xs font-normal text-[var(--text-dim)]">{count}</span>
      )}
    </h3>
  )
}

export function AppCatalog() {
  const appsQuery = useApps()
  const indexQuery = useAppIndex()
  const capsQuery = useKaiCapabilities()
  const [configApp, setConfigApp] = useState<RegisteredApp | null>(null)
  const [installApp, setInstallApp] = useState<IndexApp | null>(null)

  const tasks = useMemo(() => availableTasks(capsQuery.data), [capsQuery.data])
  const apps = appsQuery.data ?? []

  // Available = index entries not yet installed. Entries with installed=true are
  // already registered and render in the Installed group (deduped by id there).
  const available = useMemo(
    () => (indexQuery.data ?? []).filter((a) => !a.installed),
    [indexQuery.data]
  )

  const refresh = () => {
    appsQuery.refetch()
    indexQuery.refetch()
  }

  return (
    <section className="space-y-6">
      <PageHeader
        title="App Store"
        description="Detector apps built on the OpenNVR App SDK. Enable, configure, and monitor installed apps, or browse the index for more to install — each card checks its required AI tasks against the adapters registered with KAI-C."
        actions={
          <Button onClick={refresh} disabled={appsQuery.isPending || indexQuery.isFetching}>
            <RefreshCw size={14} className={appsQuery.isFetching || indexQuery.isFetching ? 'animate-spin' : ''} /> Refresh
          </Button>
        }
      />

      {/* --------------------------- Installed --------------------------- */}
      <div className="space-y-3">
        <GroupHeader title="Installed" count={apps.length} />
        {appsQuery.isPending ? (
          <SkeletonGrid count={6} />
        ) : appsQuery.isError ? (
          <ErrorCard
            title="App registry unavailable"
            message={extractApiError(appsQuery.error, 'Could not load the app registry.')}
            onRetry={() => appsQuery.refetch()}
          />
        ) : apps.length === 0 ? (
          <EmptyState
            icon={<Boxes size={28} />}
            title="No apps installed yet"
            description="Apps self-register on boot; install one from the index below or see sdk/opennvr-app-sdk to build your own."
          />
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3">
            {apps.map((app) => (
              <AppCard key={app.id} app={app} tasks={tasks} onConfigure={() => setConfigApp(app)} />
            ))}
          </div>
        )}
      </div>

      {/* ---------------------- Available to install --------------------- */}
      {/* Best-effort: if the index endpoint errors, we simply omit this group
          rather than blanking the page above. */}
      {!indexQuery.isError && (
        <div className="space-y-3">
          <GroupHeader title="Available to install" count={available.length} />
          {indexQuery.isPending ? (
            <SkeletonGrid count={3} />
          ) : available.length === 0 ? (
            <EmptyState
              icon={<Boxes size={28} />}
              title="No additional apps available"
              description="Every app in the index is already installed."
            />
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3">
              {available.map((app) => (
                <AvailableAppCard key={app.id} app={app} tasks={tasks} onInstall={() => setInstallApp(app)} />
              ))}
            </div>
          )}
        </div>
      )}

      {configApp && <AppConfigModal key={configApp.id} app={configApp} onClose={() => setConfigApp(null)} />}
      {installApp && <InstallModal key={installApp.id} app={installApp} onClose={() => setInstallApp(null)} />}
    </section>
  )
}
