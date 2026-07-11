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

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Play,
  Pause,
  SkipBack,
  SkipForward,
  Rewind,
  FastForward,
  Maximize2,
  Minimize2,
  Volume2,
  VolumeX,
  Loader2,
  X,
  AlertCircle,
} from 'lucide-react'
import { apiService } from '../lib/apiService'
import { PlaybackTimeline, type TimelineSegment } from './PlaybackTimeline'

interface RawSegment {
  start: string
  duration: number
  playback_url: string
}

interface Seg {
  startMs: number
  endMs: number
  startIso: string
  duration: number
}

interface PlaybackConsoleProps {
  cameraId: number
  cameraName: string
  /** YYYY-MM-DD */
  date: string
  onClose: () => void
}

// Zoom presets (visible span in ms) — mirrors the "1 Day / …" control.
const ZOOMS: { label: string; span: number }[] = [
  { label: '24h', span: 24 * 3600_000 },
  { label: '6h', span: 6 * 3600_000 },
  { label: '1h', span: 3600_000 },
  { label: '10m', span: 600_000 },
  { label: '2m', span: 120_000 },
]

const SPEEDS = [0.5, 1, 2, 4, 8]
const FRAME_STEP = 1 / 25 // ~1 frame at 25fps

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

export function PlaybackConsole({ cameraId, cameraName, date, onClose }: PlaybackConsoleProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  // Wall-clock instant (epoch ms) the currently-loaded MediaMTX /get window began at.
  const windowStartRef = useRef<number>(0)

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [segs, setSegs] = useState<Seg[]>([])
  const [base, setBase] = useState('')
  const [path, setPath] = useState('')

  const [dayStart, setDayStart] = useState(0)
  const [dayEnd, setDayEnd] = useState(0)
  const [view, setView] = useState<{ start: number; end: number }>({ start: 0, end: 0 })
  const [zoomIdx, setZoomIdx] = useState(0)

  const [currentMs, setCurrentMs] = useState(0)
  const [previewMs, setPreviewMs] = useState<number | null>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [rateIdx, setRateIdx] = useState(1) // 1x
  const [muted, setMuted] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [buffering, setBuffering] = useState(false)

  const timelineSegs: TimelineSegment[] = useMemo(
    () => segs.map((s) => ({ startMs: s.startMs, endMs: s.endMs })),
    [segs]
  )

  // ---- Load the day's segments ---------------------------------------------
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    apiService
      .getSegments(cameraId, date)
      .then((res: any) => {
        if (cancelled) return
        const raw: RawSegment[] = res.data?.segments || []
        const parsed: Seg[] = raw
          .map((r) => {
            const startMs = Date.parse(r.start)
            return {
              startMs,
              endMs: startMs + (r.duration || 0) * 1000,
              startIso: r.start,
              duration: r.duration || 0,
            }
          })
          .filter((s) => Number.isFinite(s.startMs))
          .sort((a, b) => a.startMs - b.startMs)

        setBase((res.data?.playback_base_url || '').replace(/\/$/, ''))
        setPath(res.data?.path || '')
        setSegs(parsed)

        if (parsed.length === 0) {
          setError('No recordings found for this day.')
          setLoading(false)
          return
        }

        // Day window = local calendar day that contains the first clip.
        const d = new Date(parsed[0].startMs)
        d.setHours(0, 0, 0, 0)
        const ds = d.getTime()
        const de = ds + 24 * 3600_000
        setDayStart(ds)
        setDayEnd(de)
        setView({ start: ds, end: de })
        setZoomIdx(0)
        setCurrentMs(parsed[0].startMs)
        windowStartRef.current = parsed[0].startMs
        setLoading(false)
      })
      .catch((e: any) => {
        if (cancelled) return
        setError(e?.message || 'Failed to load recordings')
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [cameraId, date])

  // ---- Source loading -------------------------------------------------------
  const buildUrl = useCallback(
    (startIso: string, durationSec: number) =>
      `${base}/get?path=${path}&start=${encodeURIComponent(startIso)}&duration=${durationSec}`,
    [base, path]
  )

  /** Load a MediaMTX window starting at wall-clock `ms` and (optionally) play. */
  const loadFrom = useCallback(
    (ms: number, play: boolean) => {
      const el = videoRef.current
      if (!el || segs.length === 0) return

      const containing = segs.find((s) => ms >= s.startMs && ms < s.endMs)
      let winStart: number
      let startIso: string
      let durationSec: number

      if (containing) {
        const atStart = Math.abs(ms - containing.startMs) < 500
        winStart = atStart ? containing.startMs : ms
        startIso = atStart ? containing.startIso : new Date(ms).toISOString()
        durationSec = Math.max(1, (containing.endMs - winStart) / 1000 + 0.5)
      } else {
        // In a gap → jump to the next available clip.
        const next = segs.find((s) => s.startMs >= ms)
        if (!next) {
          el.pause()
          return
        }
        winStart = next.startMs
        startIso = next.startIso
        durationSec = Math.max(1, next.duration + 0.5)
      }

      windowStartRef.current = winStart
      setCurrentMs(winStart)
      el.src = buildUrl(startIso, durationSec)
      el.load()
      el.playbackRate = SPEEDS[rateIdx]
      el.muted = muted
      if (play) el.play().catch(() => {})
    },
    [segs, buildUrl, rateIdx, muted]
  )

  // Start playback once segments are ready.
  useEffect(() => {
    if (!loading && segs.length > 0 && videoRef.current && !videoRef.current.src) {
      loadFrom(segs[0].startMs, true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, segs])

  // ---- Video element events -------------------------------------------------
  useEffect(() => {
    const el = videoRef.current
    if (!el) return
    const onPlay = () => setIsPlaying(true)
    const onPause = () => setIsPlaying(false)
    const onTime = () => setCurrentMs(windowStartRef.current + el.currentTime * 1000)
    const onWaiting = () => setBuffering(true)
    const onPlaying = () => setBuffering(false)
    const onCanPlay = () => setBuffering(false)
    const onEnded = () => {
      // Advance to the next clip after the current window (skips the gap).
      const after = windowStartRef.current + (el.duration || 0) * 1000
      const next = segs.find((s) => s.startMs >= after - 500)
      if (next) {
        loadFrom(next.startMs, true)
      } else {
        setIsPlaying(false)
      }
    }
    el.addEventListener('play', onPlay)
    el.addEventListener('pause', onPause)
    el.addEventListener('timeupdate', onTime)
    el.addEventListener('waiting', onWaiting)
    el.addEventListener('playing', onPlaying)
    el.addEventListener('canplay', onCanPlay)
    el.addEventListener('ended', onEnded)
    return () => {
      el.removeEventListener('play', onPlay)
      el.removeEventListener('pause', onPause)
      el.removeEventListener('timeupdate', onTime)
      el.removeEventListener('waiting', onWaiting)
      el.removeEventListener('playing', onPlaying)
      el.removeEventListener('canplay', onCanPlay)
      el.removeEventListener('ended', onEnded)
    }
  }, [segs, loadFrom])

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      const el = videoRef.current
      if (el) {
        el.pause()
        el.removeAttribute('src')
        el.load()
      }
    }
  }, [])

  // Fullscreen tracking.
  useEffect(() => {
    const onFs = () => setIsFullscreen(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', onFs)
    return () => document.removeEventListener('fullscreenchange', onFs)
  }, [])

  // Keep the playhead within the visible window while playing (auto-follow).
  useEffect(() => {
    if (previewMs != null) return
    if (currentMs < view.start || currentMs > view.end) {
      const span = view.end - view.start
      if (span <= 0) return
      let start = clamp(currentMs - span / 2, dayStart, dayEnd - span)
      if (span >= dayEnd - dayStart) start = dayStart
      setView({ start, end: start + span })
    }
  }, [currentMs, previewMs, view.start, view.end, dayStart, dayEnd])

  // ---- Controls -------------------------------------------------------------
  const togglePlay = () => {
    const el = videoRef.current
    if (!el) return
    if (el.paused) el.play().catch(() => {})
    else el.pause()
  }

  const seekTo = (ms: number) => loadFrom(ms, isPlaying || true)

  const stepFrame = (dir: 1 | -1) => {
    const el = videoRef.current
    if (!el) return
    el.pause()
    el.currentTime = clamp(el.currentTime + dir * FRAME_STEP, 0, el.duration || 0)
  }

  const changeSpeed = (dir: 1 | -1) => {
    const next = clamp(rateIdx + dir, 0, SPEEDS.length - 1)
    setRateIdx(next)
    if (videoRef.current) videoRef.current.playbackRate = SPEEDS[next]
  }

  const toggleMute = () => {
    const el = videoRef.current
    if (!el) return
    el.muted = !el.muted
    setMuted(el.muted)
  }

  const toggleFullscreen = () => {
    if (document.fullscreenElement) document.exitFullscreen?.()
    else containerRef.current?.requestFullscreen?.()
  }

  const applyZoom = (idx: number) => {
    const span = ZOOMS[idx].span
    setZoomIdx(idx)
    const center = previewMs ?? currentMs
    const total = dayEnd - dayStart
    if (span >= total) {
      setView({ start: dayStart, end: dayEnd })
      return
    }
    const start = clamp(center - span / 2, dayStart, dayEnd - span)
    setView({ start, end: start + span })
  }

  const effectiveCurrent = previewMs ?? currentMs

  return (
    <div className="fixed inset-0 bg-black/85 flex items-center justify-center z-50 p-4">
      <div
        ref={containerRef}
        className="bg-[var(--panel)] border border-neutral-700 w-full max-w-5xl flex flex-col"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-neutral-700 bg-[var(--panel-2)]">
          <div className="flex items-center gap-2 min-w-0">
            <Play size={16} className="text-[var(--accent)] shrink-0" />
            <span className="font-medium truncate">{cameraName}</span>
            <span className="text-xs text-[var(--text-dim)] shrink-0">· {date}</span>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 hover:bg-[var(--panel)] rounded transition-colors"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {/* Video */}
        <div className="relative bg-black aspect-video flex items-center justify-center">
          {error ? (
            <div className="text-center p-8">
              <AlertCircle size={48} className="mx-auto mb-3 text-amber-400 opacity-70" />
              <p className="text-neutral-300 text-sm">{error}</p>
            </div>
          ) : (
            <>
              <video
                ref={videoRef}
                className="w-full h-full object-contain"
                playsInline
                crossOrigin="anonymous"
                onClick={togglePlay}
              />
              {(loading || buffering) && (
                <div className="absolute inset-0 flex items-center justify-center bg-black/30 pointer-events-none">
                  <Loader2 size={40} className="animate-spin text-[var(--accent)]" />
                </div>
              )}
            </>
          )}
        </div>

        {/* Toolbar */}
        {!error && (
          <div className="flex items-center gap-1 px-3 py-2 bg-[var(--panel-2)] border-t border-neutral-700">
            <IconBtn title="Previous frame" onClick={() => stepFrame(-1)}>
              <SkipBack size={16} />
            </IconBtn>
            <IconBtn title={isPlaying ? 'Pause' : 'Play'} onClick={togglePlay}>
              {isPlaying ? <Pause size={18} /> : <Play size={18} />}
            </IconBtn>
            <IconBtn title="Next frame" onClick={() => stepFrame(1)}>
              <SkipForward size={16} />
            </IconBtn>

            <div className="mx-1 flex items-center gap-1">
              <IconBtn title="Slower" onClick={() => changeSpeed(-1)}>
                <Rewind size={16} />
              </IconBtn>
              <span className="text-xs font-mono w-8 text-center tabular-nums">
                {SPEEDS[rateIdx]}x
              </span>
              <IconBtn title="Faster" onClick={() => changeSpeed(1)}>
                <FastForward size={16} />
              </IconBtn>
            </div>

            <IconBtn title={muted ? 'Unmute' : 'Mute'} onClick={toggleMute}>
              {muted ? <VolumeX size={16} /> : <Volume2 size={16} />}
            </IconBtn>

            <div className="flex-1" />

            {/* Zoom control */}
            <div className="flex items-center rounded overflow-hidden border border-neutral-700">
              {ZOOMS.map((z, i) => (
                <button
                  key={z.label}
                  onClick={() => applyZoom(i)}
                  className={`px-2 py-1 text-[11px] font-mono transition-colors ${
                    i === zoomIdx
                      ? 'bg-[var(--accent)] text-white'
                      : 'text-[var(--text-dim)] hover:bg-[var(--panel)]'
                  }`}
                >
                  {z.label}
                </button>
              ))}
            </div>

            <IconBtn
              title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
              onClick={toggleFullscreen}
            >
              {isFullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
            </IconBtn>
          </div>
        )}

        {/* Timeline */}
        {!error && segs.length > 0 && view.end > view.start && (
          <div className="px-3 pt-1 pb-3 bg-[var(--panel-2)]">
            <PlaybackTimeline
              segments={timelineSegs}
              viewStart={view.start}
              viewEnd={view.end}
              currentTime={effectiveCurrent}
              onSeek={seekTo}
              onScrubPreview={setPreviewMs}
            />
          </div>
        )}
      </div>
    </div>
  )
}

function IconBtn({
  title,
  onClick,
  children,
}: {
  title: string
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      title={title}
      onClick={onClick}
      className="p-1.5 rounded text-[var(--text)] hover:bg-[var(--panel)] transition-colors"
    >
      {children}
    </button>
  )
}
