'use client'

import * as React from 'react'
import { Maximize2, AlertTriangle } from 'lucide-react'
import { DiagramViewer } from './DiagramViewer'

// Module-scope guard: mermaid.initialize is global, so calling it on every
// block would clobber settings under concurrent renders. We also want
// `suppressErrorRendering: true` set before any render call so mermaid's
// internal renderer never injects its built-in "Syntax error in text" SVG
// into document.body on parse failures — we render our own error UI instead.
let mermaidInitPromise: Promise<typeof import('mermaid').default> | null = null

function loadMermaid() {
  if (!mermaidInitPromise) {
    mermaidInitPromise = import('mermaid').then(({ default: mermaid }) => {
      mermaid.initialize({
        startOnLoad: false,
        theme: 'neutral',
        securityLevel: 'loose',
        suppressErrorRendering: true,
      })
      return mermaid
    })
  }
  return mermaidInitPromise
}

type RenderState =
  | { kind: 'loading' }
  | { kind: 'ok'; svg: string }
  | { kind: 'error'; message: string }

export function MermaidBlock({ chart }: { chart: string }) {
  const containerRef = React.useRef<HTMLDivElement>(null)
  const idRef = React.useRef(`mermaid-${Math.random().toString(36).slice(2, 9)}`)
  const [state, setState] = React.useState<RenderState>({ kind: 'loading' })
  const [fullscreen, setFullscreen] = React.useState(false)
  const [showSource, setShowSource] = React.useState(false)

  React.useEffect(() => {
    let cancelled = false
    setState({ kind: 'loading' })

    loadMermaid()
      .then(async (mermaid) => {
        // Parse first to validate without touching the DOM. With
        // suppressErrors:true mermaid.parse returns false on invalid input
        // instead of throwing — and crucially does not render anything.
        const parseResult = await mermaid.parse(chart, { suppressErrors: true })
        if (cancelled) return
        if (parseResult === false) {
          setState({ kind: 'error', message: 'Diagram could not be parsed.' })
          return
        }

        try {
          const { svg } = await mermaid.render(idRef.current, chart)
          if (cancelled) return
          setState({ kind: 'ok', svg })
          if (containerRef.current) {
            containerRef.current.innerHTML = svg
          }
        } catch (err) {
          if (cancelled) return
          const message = err instanceof Error ? err.message : 'Diagram render failed.'
          setState({ kind: 'error', message })
        }
      })
      .catch((err) => {
        if (cancelled) return
        const message = err instanceof Error ? err.message : 'Failed to load diagram renderer.'
        setState({ kind: 'error', message })
      })

    return () => {
      cancelled = true
      // Drop any stale orphan node mermaid may have left behind under the
      // deterministic id. Belt-and-braces with suppressErrorRendering.
      const orphan = document.getElementById(idRef.current)
      if (orphan && orphan.parentElement === document.body) {
        orphan.remove()
      }
    }
  }, [chart])

  if (state.kind === 'error') {
    return (
      <div className="my-6 rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 text-sm">
        <div className="flex items-start gap-2 text-amber-700 dark:text-amber-400">
          <AlertTriangle className="size-4 shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0">
            <div className="font-medium">Mermaid diagram failed to render</div>
            <div className="text-xs text-muted-foreground mt-0.5 break-words">
              {state.message}
            </div>
            <button
              type="button"
              onClick={() => setShowSource((s) => !s)}
              className="mt-2 text-xs text-muted-foreground/70 hover:text-foreground underline underline-offset-2 cursor-pointer"
            >
              {showSource ? 'Hide source' : 'Show source'}
            </button>
            {showSource && (
              <pre className="mt-2 text-[12px] leading-relaxed bg-muted/60 border border-border rounded-md p-3 overflow-x-auto whitespace-pre-wrap">
                {chart}
              </pre>
            )}
          </div>
        </div>
      </div>
    )
  }

  return (
    <>
      <div
        className="my-6 relative group"
        onClick={() => state.kind === 'ok' && setFullscreen(true)}
      >
        <div
          ref={containerRef}
          className="flex justify-center [&_svg]:max-w-full cursor-pointer"
        />
        {state.kind === 'ok' && (
          <button
            onClick={(e) => { e.stopPropagation(); setFullscreen(true) }}
            className="absolute top-2 right-2 p-1.5 rounded-md bg-background/80 border border-border text-muted-foreground hover:text-foreground opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer"
            title="View fullscreen"
          >
            <Maximize2 className="size-3.5" />
          </button>
        )}
      </div>

      {fullscreen && state.kind === 'ok' && (
        <DiagramViewer content={state.svg} type="svg" onClose={() => setFullscreen(false)} />
      )}
    </>
  )
}
