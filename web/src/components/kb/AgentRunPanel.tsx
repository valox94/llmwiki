'use client'

import * as React from 'react'
import {
  Sparkles, Trash2, X, Loader2, CheckCircle2, AlertCircle, Wrench, FileText,
  ChevronDown, ChevronRight,
} from 'lucide-react'

import { useUserStore } from '@/stores/useUserStore'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const isLocal = process.env.NEXT_PUBLIC_MODE === 'local'

// ── Event shapes ────────────────────────────────────────────────────────
// Mirror AgentEvent from api/services/agent_runner.py — kept duck-typed
// (any extra fields are ignored, unknown `type`s render generically) so that
// adding a new event server-side never breaks the UI.

type AssistantBlock =
  | { type: 'text'; text: string }
  | { type: 'tool_use'; name: string; input: unknown; id?: string }
  | { type: 'tool_result'; tool_use_id?: string; content: unknown; is_error?: boolean }
  | { type: string; [k: string]: unknown }

type CitingPage = { id: string; path: string; filename: string; title?: string | null }

type AgentRunEvent =
  | { type: 'starting'; workspace?: string; doc_id?: string; filename?: string; fast_path?: boolean }
  | { type: 'queued'; message?: string }
  | { type: 'blast_radius'; citing_count: number; citing: CitingPage[] }
  | { type: 'agent_created'; agent_id?: string }
  | { type: 'run_started'; run_id?: string }
  | { type: 'assistant'; blocks: AssistantBlock[] }
  | { type: 'finished'; status?: string; run_id?: string; agent_id?: string; fast_path?: boolean }
  | { type: 'error'; phase?: string; message?: string; retryable?: boolean }
  | { type: string; [k: string]: unknown }

export type AgentAction = 'ingest' | 'deprecate'

const ACTION_META: Record<AgentAction, { title: string; endpoint: (id: string) => string; icon: React.ComponentType<{ className?: string }> }> = {
  ingest: {
    title: 'Ingest with agent',
    endpoint: (id) => `${API_URL}/v1/agents/ingest/${id}`,
    icon: Sparkles,
  },
  deprecate: {
    title: 'Deprecate with agent',
    endpoint: (id) => `${API_URL}/v1/agents/deprecate/${id}`,
    icon: Trash2,
  },
}

// ── Panel ───────────────────────────────────────────────────────────────

export function AgentRunPanel({
  action, docId, filename, onClose, onFinished,
}: {
  action: AgentAction
  docId: string
  filename: string
  onClose: () => void
  /** Fired exactly once when the run reaches a `finished` terminal state.
   *  Used by callers to refresh data (e.g. drop the deleted source from the
   *  document list after a successful deprecate run). */
  onFinished?: () => void
}) {
  const meta = ACTION_META[action]
  const [events, setEvents] = React.useState<AgentRunEvent[]>([])
  const [status, setStatus] = React.useState<'starting' | 'running' | 'finished' | 'error'>('starting')
  const containerRef = React.useRef<HTMLDivElement | null>(null)
  const finishedRef = React.useRef(false)

  React.useEffect(() => {
    const ac = new AbortController()
    const append = (e: AgentRunEvent) => setEvents((prev) => [...prev, e])

    const stream = async () => {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      if (!isLocal) {
        const token = useUserStore.getState().accessToken
        if (token) headers.Authorization = `Bearer ${token}`
      }

      let res: Response
      try {
        res = await fetch(meta.endpoint(docId), { method: 'POST', headers, signal: ac.signal })
      } catch (err) {
        if ((err as Error).name === 'AbortError') return
        append({ type: 'error', phase: 'network', message: (err as Error).message })
        setStatus('error')
        return
      }

      if (!res.ok || !res.body) {
        let detail = `HTTP ${res.status}`
        try {
          const body = await res.json()
          if (typeof body?.detail === 'string') detail = body.detail
        } catch { /* non-JSON body */ }
        append({ type: 'error', phase: 'http', message: detail })
        setStatus('error')
        return
      }

      setStatus('running')
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        const frames = buffer.split('\n\n')
        buffer = frames.pop() ?? ''

        for (const frame of frames) {
          const line = frame.split('\n').find((l) => l.startsWith('data: '))
          if (!line) continue
          try {
            const event = JSON.parse(line.slice(6)) as AgentRunEvent
            append(event)
            if (event.type === 'finished') {
              setStatus('finished')
              if (!finishedRef.current) {
                finishedRef.current = true
                onFinished?.()
              }
            }
            if (event.type === 'error') setStatus('error')
          } catch (err) {
            append({ type: 'error', phase: 'parse', message: `Bad SSE frame: ${(err as Error).message}` })
            setStatus('error')
          }
        }
      }
    }

    stream()
    return () => ac.abort()
    // onFinished intentionally omitted from deps — we want it to fire once
    // even if the parent re-creates the callback on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action, docId])

  React.useEffect(() => {
    const el = containerRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [events])

  const Icon = meta.icon

  return (
    <div className="fixed right-0 top-0 bottom-0 w-[28rem] max-w-[90vw] bg-background border-l border-border shadow-xl flex flex-col z-50">
      <header className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <Icon className="size-4 text-foreground shrink-0" />
          <div className="flex flex-col min-w-0">
            <span className="text-sm font-medium leading-tight">{meta.title}</span>
            <span className="text-xs text-muted-foreground truncate" title={filename}>{filename}</span>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <StatusBadge status={status} />
          <button
            onClick={onClose}
            className="p-1 hover:bg-accent rounded text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            title="Close"
          >
            <X className="size-4" />
          </button>
        </div>
      </header>

      <div ref={containerRef} className="flex-1 min-h-0 overflow-y-auto p-3 space-y-2">
        {events.length === 0 && status === 'starting' && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground p-2">
            <Loader2 className="size-3.5 animate-spin" />
            <span>Connecting to agent...</span>
          </div>
        )}
        {events.map((event, i) => <EventRow key={i} event={event} />)}
      </div>
    </div>
  )
}

// ── Status pill ─────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: 'starting' | 'running' | 'finished' | 'error' }) {
  if (status === 'finished') {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-green-700 dark:text-green-400">
        <CheckCircle2 className="size-3.5" />
        Done
      </span>
    )
  }
  if (status === 'error') {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-destructive">
        <AlertCircle className="size-3.5" />
        Error
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
      <Loader2 className="size-3.5 animate-spin" />
      {status === 'starting' ? 'Starting' : 'Running'}
    </span>
  )
}

// ── Event renderer ──────────────────────────────────────────────────────

function EventRow({ event }: { event: AgentRunEvent }) {
  if (event.type === 'assistant') {
    const blocks = (event as { blocks: AssistantBlock[] }).blocks ?? []
    return <>{blocks.map((b, i) => <AssistantBlockRow key={i} block={b} />)}</>
  }

  if (event.type === 'blast_radius') {
    const e = event as { citing_count: number; citing: CitingPage[] }
    if (e.citing_count === 0) {
      return (
        <div className="text-xs italic text-muted-foreground px-2">
          No wiki pages cite this source — fast-path delete (no agent needed).
        </div>
      )
    }
    return (
      <div className="text-xs px-2 py-1.5 bg-muted/40 rounded-md border border-border/50">
        <div className="font-medium text-foreground">Impact: {e.citing_count} wiki page{e.citing_count === 1 ? '' : 's'} will be updated</div>
        <ul className="mt-1 space-y-0.5 text-muted-foreground">
          {e.citing.map((c) => (
            <li key={c.id}>• <code className="text-[11px]">{c.path}{c.filename}</code> {c.title ? `— ${c.title}` : ''}</li>
          ))}
        </ul>
      </div>
    )
  }

  if (event.type === 'starting' || event.type === 'agent_created' || event.type === 'run_started' || event.type === 'queued') {
    const label =
      event.type === 'starting' && (event as { fast_path?: boolean }).fast_path ? 'Fast-path delete (skipping agent)' :
      event.type === 'starting' ? 'Starting agent...' :
      event.type === 'agent_created' ? `Agent created (${truncate((event as { agent_id?: string }).agent_id, 12)})` :
      event.type === 'run_started' ? `Run started (${truncate((event as { run_id?: string }).run_id, 12)})` :
      (event as { message?: string }).message ?? 'Queued'
    return <div className="text-xs text-muted-foreground italic px-2">{label}</div>
  }

  if (event.type === 'finished') {
    const e = event as { status?: string; fast_path?: boolean }
    const label = e.fast_path ? 'Source deleted (no wiki updates needed)' : `Run finished: `
    return (
      <div className="text-sm flex items-center gap-2 px-2 pt-2 border-t border-border mt-2">
        <CheckCircle2 className="size-4 text-green-600 dark:text-green-400" />
        <span>
          {label}
          {!e.fast_path && <code className="text-xs bg-muted px-1.5 py-0.5 rounded ml-1">{e.status ?? 'finished'}</code>}
        </span>
      </div>
    )
  }

  if (event.type === 'error') {
    const e = event as { phase?: string; message?: string }
    return (
      <div className="text-sm flex items-start gap-2 px-2 pt-2 border-t border-border mt-2">
        <AlertCircle className="size-4 text-destructive shrink-0 mt-0.5" />
        <div className="min-w-0">
          <div className="font-medium">Error{e.phase ? ` (${e.phase})` : ''}</div>
          <div className="text-xs text-muted-foreground break-words">{e.message ?? 'Unknown error'}</div>
        </div>
      </div>
    )
  }

  return (
    <details className="text-xs text-muted-foreground px-2">
      <summary className="cursor-pointer">{event.type}</summary>
      <pre className="text-[10px] bg-muted p-2 rounded mt-1 overflow-x-auto">{JSON.stringify(event, null, 2)}</pre>
    </details>
  )
}

function AssistantBlockRow({ block }: { block: AssistantBlock }) {
  if (block.type === 'text') {
    const text = (block as { text: string }).text
    if (!text.trim()) return null
    return <div className="text-sm leading-relaxed whitespace-pre-wrap px-2 py-1">{text}</div>
  }

  if (block.type === 'tool_use') {
    const b = block as { name: string; input: unknown }
    return (
      <div className="flex items-start gap-2 text-sm px-2 py-1.5 bg-muted/50 rounded-md border border-border/50">
        <Wrench className="size-3.5 text-muted-foreground shrink-0 mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="font-medium text-xs">{b.name}</div>
          <pre className="text-[10px] text-muted-foreground mt-1 overflow-x-auto whitespace-pre-wrap break-words">{JSON.stringify(b.input, null, 2)}</pre>
        </div>
      </div>
    )
  }

  if (block.type === 'tool_result') {
    const b = block as { content: unknown; is_error?: boolean }
    return <ToolResultRow content={b.content} isError={!!b.is_error} />
  }

  return (
    <details className="text-xs text-muted-foreground px-2">
      <summary className="cursor-pointer">{block.type}</summary>
      <pre className="text-[10px] bg-muted p-2 rounded mt-1 overflow-x-auto">{JSON.stringify(block, null, 2)}</pre>
    </details>
  )
}

function ToolResultRow({ content, isError }: { content: unknown; isError: boolean }) {
  const [open, setOpen] = React.useState(false)
  const text = React.useMemo(() => extractToolResultText(content), [content])
  const preview = text.split('\n').slice(0, 2).join(' ').slice(0, 120)
  const isLong = text.length > 120 || text.split('\n').length > 2

  return (
    <div className={`flex items-start gap-2 text-sm px-2 py-1.5 rounded-md border ${isError ? 'border-destructive/40 bg-destructive/5' : 'border-border/50 bg-muted/30'}`}>
      <FileText className={`size-3.5 shrink-0 mt-0.5 ${isError ? 'text-destructive' : 'text-muted-foreground'}`} />
      <div className="min-w-0 flex-1">
        {isLong ? (
          <button onClick={() => setOpen((o) => !o)} className="flex items-center gap-1 text-xs font-medium hover:underline cursor-pointer">
            {open ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
            Result {isError ? '(error)' : ''}
          </button>
        ) : (
          <div className="text-xs font-medium">Result {isError ? '(error)' : ''}</div>
        )}
        <pre className="text-[10px] text-muted-foreground mt-1 overflow-x-auto whitespace-pre-wrap break-words">{open || !isLong ? text : preview + (isLong ? '...' : '')}</pre>
      </div>
    </div>
  )
}

function extractToolResultText(content: unknown): string {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content
      .map((c) => {
        if (typeof c === 'string') return c
        if (c && typeof c === 'object' && 'text' in c) return String((c as { text: unknown }).text ?? '')
        if (c && typeof c === 'object' && 'repr' in c) return String((c as { repr: unknown }).repr ?? '')
        return JSON.stringify(c)
      })
      .join('\n\n')
  }
  if (content == null) return ''
  return JSON.stringify(content, null, 2)
}

function truncate(s: string | undefined, n: number): string {
  if (!s) return ''
  return s.length <= n ? s : s.slice(0, n) + '...'
}
