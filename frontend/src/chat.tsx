import { useEffect, useRef, useState, type FormEvent } from 'react'

export type Turn = { id: number; question: string; answer: string; createdAt: string; model: string | null; reasoning: string | null }
export type ChatHistory = { turns: Turn[]; nextBefore: number | null }
export type ChatModels = { defaultModel: string; models: string[] }
export type ChatRequest = { requestId: string; message: string; model: string }

type ChatPageProps = {
  load: (model: string, before?: number) => Promise<ChatHistory>
  loadModels: () => Promise<ChatModels>
  send: (request: ChatRequest) => Promise<Turn>
}

export function ChatPage({ load, loadModels, send }: ChatPageProps) {
  const [models, setModels] = useState<string[]>([])
  const [model, setModel] = useState('')
  const [modelError, setModelError] = useState('')
  const [sending, setSending] = useState(false)

  useEffect(() => {
    let active = true
    loadModels().then(catalog => {
      if (!active) return
      setModels(catalog.models)
      setModel(catalog.defaultModel)
    }).catch(error => {
      if (active) setModelError(error.message)
    })
    return () => { active = false }
  }, [loadModels])

  return <>
    <section className="hero chat-hero">
      <div className="eyebrow"><span /> 阿里云百炼</div>
      <h1>AI 聊天</h1>
      <p>每个账号下，各模型有独立的聊天记录和记忆。切回模型即可继续它的对话。</p>
    </section>
    <section className="panel chat-panel" aria-label="AI 对话">
      <div className="section-heading chat-heading">
        <span className="step">AI</span><h2>我的对话</h2>
        <div className="chat-model-choice">
          <label htmlFor="chat-model">聊天模型</label>
          <select id="chat-model" value={model} disabled={sending || models.length === 0}
            onChange={event => setModel(event.target.value)}>
            {models.length === 0 && <option value="">{modelError ? '暂不可用' : '正在加载…'}</option>}
            {models.map(name => <option key={name} value={name}>{name}</option>)}
          </select>
        </div>
      </div>
      {modelError && <p className="error chat-model-error" role="alert">{modelError}</p>}
      {model && <Conversation key={model} model={model} load={load} send={send} setSending={setSending} />}
    </section>
  </>
}

function Conversation({ model, load, send, setSending }: Pick<ChatPageProps, 'load' | 'send'> & {
  model: string
  setSending: (sending: boolean) => void
}) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [before, setBefore] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [draft, setDraft] = useState('')
  const [waiting, setWaiting] = useState('')
  const [error, setError] = useState('')
  const [historyError, setHistoryError] = useState('')
  const retry = useRef<ChatRequest | null>(null)
  const transcript = useRef<HTMLDivElement>(null)
  const followLatest = useRef(true)
  const input = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    let active = true
    load(model).then(history => {
      if (!active) return
      setTurns(history.turns)
      setBefore(history.nextBefore)
    }).catch(error => {
      if (active) setHistoryError(error.message)
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [load, model])

  useEffect(() => {
    if (transcript.current && followLatest.current) {
      transcript.current.scrollTop = transcript.current.scrollHeight
    }
  }, [turns, waiting])

  async function loadEarlier() {
    if (before === null || loadingOlder) return
    setLoadingOlder(true)
    setHistoryError('')
    try {
      const history = await load(model, before)
      followLatest.current = false
      setTurns(current => [...history.turns, ...current])
      setBefore(history.nextBefore)
    } catch (error) {
      setHistoryError((error as Error).message)
    } finally {
      setLoadingOlder(false)
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const message = draft.trim()
    if (!message || waiting || loading || loadingOlder || message.length > 2000) return
    const request = retry.current?.message === message
      ? retry.current : { requestId: crypto.randomUUID(), message, model }
    retry.current = request
    followLatest.current = true
    setError('')
    setWaiting(message)
    setSending(true)
    try {
      const turn = await send(request)
      setTurns(current => [...current.filter(entry => entry.id !== turn.id), turn].sort((a, b) => a.id - b.id))
      setDraft('')
      retry.current = null
      input.current?.focus()
    } catch (error) {
      setError((error as Error).message)
    } finally {
      setWaiting('')
      setSending(false)
    }
  }

  return <>
      <div className="chat-transcript" ref={transcript} role="region" aria-label="聊天记录" tabIndex={0}>
        {before !== null && <button type="button" className="quiet chat-older" onClick={loadEarlier}
          disabled={loadingOlder || !!waiting}>{loadingOlder ? '正在读取…' : '加载更早消息'}</button>}
        {loading && <p className="chat-empty" role="status">正在读取聊天记录…</p>}
        {historyError && <p className="error" role="alert">{historyError}</p>}
        {!loading && !historyError && turns.length === 0 && !waiting &&
          <p className="chat-empty">此模型还没有对话。可以从一个问题开始。</p>}
        {turns.map(turn => <div className="chat-turn" key={turn.id}>
          <div className="chat-message chat-user"><strong>我</strong><p>{turn.question}</p></div>
          <div className="chat-message chat-model">
            <strong>{turn.model || '百炼'}</strong>
            {turn.reasoning && <details className="chat-reasoning">
              <summary>推理过程</summary>
              <p>{turn.reasoning}</p>
            </details>}
            <p>{turn.answer}</p>
          </div>
        </div>)}
        {waiting && <div className="chat-turn">
          <div className="chat-message chat-user"><strong>我</strong><p>{waiting}</p></div>
          <p className="chat-pending" role="status">{model} 正在回复…</p>
        </div>}
      </div>
      <form className="chat-compose" onSubmit={submit}>
        <label className="rag-field" htmlFor="chat-message">消息</label>
        <textarea id="chat-message" ref={input} rows={3} maxLength={2000}
          placeholder="输入你想聊的内容" value={draft} readOnly={!!waiting}
          onChange={event => setDraft(event.target.value)} />
        {error && <p className="error" role="alert">{error}</p>}
        <div className="chat-actions">
          <small>消息和当前模型的相关上下文会发送给百炼。{draft.length} / 2,000 字</small>
          <button className="primary" disabled={loading || loadingOlder || !!waiting || !draft.trim()}>发送</button>
        </div>
      </form>
  </>
}
