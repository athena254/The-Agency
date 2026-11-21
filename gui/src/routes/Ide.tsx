import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Send } from 'lucide-react'
import { type ChatResponse } from '../api/client'
import { useCsrf } from '../api/pilot'
import { Row } from '../components/ui'

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  assessment?: ChatResponse['assessment']
  pending?: boolean
}

let counter = 0
function nextId() {
  counter += 1
  return `m${counter}`
}

/** Human, honest error text — never a raw status/body dump. */
function chatErrorText(status: number): string {
  if (status === 403) return 'Request rejected (missing or invalid CSRF token). Try again.'
  if (status === 413) return 'Message exceeds the maximum allowed length.'
  if (status === 502) return 'The Butler could not complete this turn.'
  if (status === 503) return 'Chat is unavailable in this configuration.'
  if (status === 504) return 'The Butler timed out before replying. Try again.'
  return `Request failed (HTTP ${status}).`
}

export function IdePage() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const csrf = useCsrf()
  const queryClient = useQueryClient()
  const sessionIdRef = useRef(crypto.randomUUID())
  const endRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    setError(null)
    const userMessage: Message = { id: nextId(), role: 'user', content: text }
    const pending: Message = { id: nextId(), role: 'assistant', content: '', pending: true }
    setMessages((prev) => [...prev, userMessage, pending])
    setBusy(true)

    try {
      const result = await fetch('/api/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(csrf ? { 'X-Prototype-CSRF': csrf } : {}),
        },
        body: JSON.stringify({ message: text, session_id: sessionIdRef.current }),
      })
      if (!result.ok) {
        if (result.status === 403) {
          // The cached CSRF token was rejected — refresh it for the next turn.
          void queryClient.invalidateQueries({ queryKey: ['csrf'] })
        }
        throw new Error(chatErrorText(result.status))
      }
      const data = (await result.json()) as ChatResponse
      setMessages((prev) =>
        prev.map((m) =>
          m.id === pending.id
            ? { ...m, content: data.response, assessment: data.assessment, pending: false }
            : m,
        ),
      )
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      setError(message)
      setMessages((prev) => prev.filter((m) => m.id !== pending.id))
    } finally {
      setBusy(false)
    }
  }, [busy, input, csrf, queryClient])

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 lg:flex-row">
      {/* Chat column */}
      <section className="mc-panel flex min-h-0 flex-1 flex-col">
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <div>
            <h2 className="text-sm font-semibold text-text-1">Isolated chat</h2>
            <div className="mc-label mt-0.5">model-only · no tools · no memory store</div>
          </div>
          <span className="mc-value text-[11px] text-text-3">in-session context</span>
        </div>

        <div
          className="min-h-0 flex-1 overflow-y-auto px-5 py-4"
          role="log"
          aria-live="polite"
          aria-relevant="additions text"
        >
          {messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center text-center">
              <div className="text-sm text-text-2">Start a conversation</div>
              <div className="mt-1 max-w-sm text-xs text-text-3">
                This is the isolated prototype path: it runs the model without tools, memory,
                or Lattice mirroring. The governed task path arrives with peer authority.
                Replies can see the earlier turns of this page session (kept in memory only,
                gone on reload).
              </div>
            </div>
          ) : (
            <ul className="space-y-4">
              {messages.map((m) => (
                <li key={m.id} className="flex gap-3">
                  <span
                    className={`mt-1 h-2 w-2 flex-none rounded-full ${
                      m.role === 'user' ? 'bg-signal' : 'bg-text-3'
                    }`}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="mc-label mb-1">{m.role === 'user' ? 'you' : 'Butler'}</div>
                    {m.pending ? (
                      <div className="flex items-center gap-2 text-sm text-text-3">
                        <span className="mc-status-dot bg-signal animate-pulse" />
                        thinking
                      </div>
                    ) : (
                      <div className="whitespace-pre-wrap text-sm text-text-1">{m.content}</div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
          <div ref={endRef} />
        </div>

        <div className="border-t border-line p-4">
          {error ? (
            <div className="mb-2 text-xs text-red" role="alert">
              {error}
            </div>
          ) : null}
          <label htmlFor="mc-ide-input" className="sr-only">
            Message the Butler
          </label>
          <p id="mc-ide-input-hint" className="sr-only">
            Press Enter to send, Shift+Enter to add a new line.
          </p>
          <div className="flex items-end gap-2">
            <textarea
              id="mc-ide-input"
              className="mc-textarea flex-1"
              placeholder="Message the Butler…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  void send()
                }
              }}
              aria-busy={busy}
              aria-describedby="mc-ide-input-hint"
            />
            <button
              type="button"
              className="mc-btn mc-btn-primary flex-none"
              onClick={() => void send()}
              disabled={busy || !input.trim()}
              aria-label="Send message"
            >
              <Send size={13} aria-hidden="true" />
              {busy ? 'Sending…' : 'Send'}
            </button>
          </div>
        </div>
      </section>

      {/* Context column */}
      <aside className="mc-panel w-full flex-none overflow-y-auto p-5 lg:w-80">
        <h2 className="text-sm font-semibold text-text-1">Last assessment</h2>
        <div className="mc-label mt-0.5">honest status, not verification</div>
        <dl className="mt-4 space-y-3">
          <Row label="Execution">
            <ToneText
              value={messages.find((m) => m.assessment)?.assessment?.execution_status ?? '—'}
            />
          </Row>
          <Row label="Verification">
            <ToneText
              value={messages.find((m) => m.assessment)?.assessment?.verification ?? '—'}
            />
          </Row>
          <Row label="Risk">
            <ToneText
              value={messages.find((m) => m.assessment)?.assessment?.risk_status ?? '—'}
            />
          </Row>
          <Row label="Independently verified">
            <ToneText
              value={
                messages.find((m) => m.assessment)?.assessment?.independently_verified
                  ? 'yes'
                  : 'no'
              }
            />
          </Row>
        </dl>
        <div className="mt-5 border-t border-line pt-4 text-xs leading-relaxed text-text-3">
          A provider reply is not task success. Independent checks and durable authorization
          are separate gates.
        </div>
      </aside>
    </div>
  )
}

function ToneText({ value }: { value: string }) {
  const tone =
    value === 'completed' || value === 'available'
      ? 'text-green'
      : value === 'not_performed' || value === 'not_assessed' || value === 'no'
        ? 'text-amber'
        : 'text-text-2'
  return <span className={tone}>{value}</span>
}
