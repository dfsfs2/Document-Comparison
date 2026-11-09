import { useEffect, useRef, useState, type FormEvent } from 'react'

export type Turn = { id: number; question: string; answer: string; createdAt: string; model: string | null; reasoning: string | null; attachments: string[] }
export type ChatHistory = { turns: Turn[]; nextBefore: number | null }
export type ChatModels = { defaultModel: string; models: string[]; visionModels: string[] }
export type ChatRequest = { requestId: string; message: string; model: string; files: File[] }
type ChatDelta = { type: 'reasoning' | 'answer'; text: string }
type StreamEvent = ChatDelta | { type: 'done'; turn: Turn } | { type: 'error'; message: string }
type ChatRow = {
  key: string | number
  question: string
  model: string | null
  attachments: string[]
  reasoning: string | null
  answer: string
  pending?: boolean
}

const fileTypes = /\.(docx|pdf|txt|md|png|jpe?g|webp)$/i
const imageTypes = /\.(png|jpe?g|webp)$/i

export async function readChatStream(response: Response, onDelta: (delta: ChatDelta) => void): Promise<Turn> {
  const reader = response.body?.pipeThrough(new TextDecoderStream()).getReader()
  if (!reader) throw new Error('无法读取流式回复，请稍后重试。')
  let buffer = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) throw new Error('回复中断，请重试这条消息。')
      buffer += value
      let boundary = buffer.indexOf('\n')
      while (boundary !== -1) {
        const line = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 1)
        const event = JSON.parse(line) as StreamEvent
        if (event.type === 'error') throw new Error(event.message)
        if (event.type === 'done') return event.turn
        onDelta(event)
        boundary = buffer.indexOf('\n')
      }
    }
  } finally {
    void reader.cancel().catch(() => undefined)
  }
}

type ChatPageProps = {
  load: (model: string, before?: number) => Promise<ChatHistory>
  loadModels: () => Promise<ChatModels>
  send: (request: ChatRequest, onDelta: (delta: ChatDelta) => void, signal: AbortSignal) => Promise<Turn>
}

export function ChatPage({ load, loadModels, send }: ChatPageProps) {
  const [models, setModels] = useState<string[]>([])
  const [visionModels, setVisionModels] = useState<string[]>([])
  const [model, setModel] = useState('')
  const [modelError, setModelError] = useState('')
  const [sending, setSending] = useState(false)
  const [draft, setDraft] = useState('')
  const [files, setFiles] = useState<File[]>([])

  useEffect(() => {
    let active = true
    loadModels().then(catalog => {
      if (!active) return
      setModels(catalog.models)
      setVisionModels(catalog.visionModels ?? [])
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
            {models.map(name => <option key={name} value={name}>{name}{visionModels.includes(name) ? ' · 支持图片' : ''}</option>)}
          </select>
        </div>
      </div>
      {modelError && <p className="error chat-model-error" role="alert">{modelError}</p>}
      {model && <Conversation key={model} model={model} visionModels={visionModels} load={load} send={send}
        setSending={setSending} draft={draft} setDraft={setDraft} files={files} setFiles={setFiles} />}
    </section>
  </>
}

function Conversation({ model, visionModels, load, send, setSending, draft, setDraft, files, setFiles }: Pick<ChatPageProps, 'load' | 'send'> & {
  model: string
  visionModels: string[]
  setSending: (sending: boolean) => void
  draft: string
  setDraft: (draft: string) => void
  files: File[]
  setFiles: (files: File[]) => void
}) {
  const [turns, setTurns] = useState<(Turn & { streamKey?: string })[]>([])
  const [before, setBefore] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [waiting, setWaiting] = useState<ChatRow | null>(null)
  const [error, setError] = useState('')
  const [fileError, setFileError] = useState('')
  const [historyError, setHistoryError] = useState('')
  const retry = useRef<ChatRequest | null>(null)
  const activeRequest = useRef<AbortController | null>(null)
  const transcript = useRef<HTMLDivElement>(null)
  const followLatest = useRef(true)
  const input = useRef<HTMLTextAreaElement>(null)
  const imageNeedsModel = files.some(file => imageTypes.test(file.name)) && !visionModels.includes(model)
  const rows: ChatRow[] = turns.map(turn => ({ ...turn, key: turn.streamKey ?? turn.id }))
  if (waiting) rows.push(waiting)

  useEffect(() => () => activeRequest.current?.abort(), [])

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
    if ((!message && files.length === 0) || waiting || loading || loadingOlder || imageNeedsModel || message.length > 2000) return
    const request = retry.current?.message === message && retry.current.files === files
      ? retry.current : { requestId: crypto.randomUUID(), message, model, files }
    const controller = new AbortController()
    activeRequest.current = controller
    retry.current = request
    followLatest.current = true
    setError('')
    setWaiting({ key: request.requestId, question: message || '请查看附件并概述主要内容。',
      model, attachments: files.map(file => file.name), reasoning: '', answer: '', pending: true })
    setSending(true)
    try {
      const turn = await send(request, ({ type, text }) => {
        setWaiting(current => current ? { ...current, [type]: (current[type] ?? '') + text } : current)
      }, controller.signal)
      setTurns(current => [...current.filter(entry => entry.id !== turn.id), { ...turn, streamKey: request.requestId }]
        .sort((a, b) => a.id - b.id))
      setDraft('')
      setFiles([])
      setFileError('')
      retry.current = null
      input.current?.focus()
    } catch (error) {
      if (!controller.signal.aborted) setError((error as Error).message)
    } finally {
      activeRequest.current = null
      setWaiting(null)
      setSending(false)
    }
  }

  function addFiles(selected: FileList | null) {
    if (!selected?.length) return
    const next = [...files, ...selected]
    if (next.some(file => !fileTypes.test(file.name))) {
      setFileError('附件支持 DOCX、PDF、TXT、Markdown、PNG、JPEG 和 WebP。')
    } else if (next.length > 3 || next.some(file => !file.size) || next.reduce((sum, file) => sum + file.size, 0) > 10 * 1024 * 1024) {
      setFileError('每条消息最多 3 个非空附件，总计最多 10 MB。')
    } else {
      setFiles(next)
      setFileError('')
    }
  }

  return <>
      <div className="chat-transcript" ref={transcript} role="region" aria-label="聊天记录" tabIndex={0}
        onScroll={event => {
          const box = event.currentTarget
          followLatest.current = box.scrollHeight - box.clientHeight - box.scrollTop < 40
        }}>
        {before !== null && <button type="button" className="quiet chat-older" onClick={loadEarlier}
          disabled={loadingOlder || !!waiting}>{loadingOlder ? '正在读取…' : '加载更早消息'}</button>}
        {loading && <p className="chat-empty" role="status">正在读取聊天记录…</p>}
        {historyError && <p className="error" role="alert">{historyError}</p>}
        {!loading && !historyError && turns.length === 0 && !waiting &&
          <p className="chat-empty">此模型还没有对话。可以从一个问题开始。</p>}
        {rows.map(turn => <ChatTurn key={turn.key} turn={turn} />)}
      </div>
      <form className="chat-compose" onSubmit={submit}>
        <label className="rag-field" htmlFor="chat-message">消息</label>
        <textarea id="chat-message" ref={input} rows={3} maxLength={2000}
          placeholder="输入你想聊的内容" value={draft} readOnly={!!waiting}
          onChange={event => setDraft(event.target.value)} />
        <label className="chat-file-picker">
          <span>添加附件</span>
          <input type="file" multiple disabled={!!waiting} accept=".docx,.pdf,.txt,.md,.png,.jpg,.jpeg,.webp"
            onChange={event => {
              addFiles(event.currentTarget.files)
              event.currentTarget.value = ''
            }} />
        </label>
        {files.length > 0 && <ul className="chat-files" aria-label="待发送附件">
          {files.map((file, index) => <li key={index}>
            <span>{file.name}</span>
            <button type="button" disabled={!!waiting} aria-label={`移除 ${file.name}`}
              onClick={() => { setFiles(files.filter((_, i) => i !== index)); setFileError('') }}>×</button>
          </li>)}
        </ul>}
        {fileError && <p className="error" role="alert">{fileError}</p>}
        {imageNeedsModel && <p className="error" role="status">{visionModels.length
          ? `请切换到支持图片的模型（${visionModels.join('、')}）后发送。`
          : '当前模型列表没有可用的图片模型，请移除图片后发送。'}</p>}
        {error && <p className="error" role="alert">{error}</p>}
        <div className="chat-actions">
          <small>消息、附件内容和当前模型的相关对话会发送给百炼。最多 3 个附件，总计 10 MB。{draft.length} / 2,000 字</small>
          <button className="primary" disabled={loading || loadingOlder || !!waiting || imageNeedsModel || (!draft.trim() && files.length === 0)}>发送</button>
        </div>
      </form>
  </>
}

function ChatTurn({ turn }: { turn: ChatRow }) {
  return <div className="chat-turn">
    <div className="chat-message chat-user">
      <strong>我</strong><p>{turn.question}</p>
      {turn.attachments.length > 0 && <ul className="chat-files" aria-label="已发送附件">
        {turn.attachments.map((name, index) => <li key={index}>{name}</li>)}
      </ul>}
    </div>
    <div className="chat-message chat-model">
      <strong>{turn.model || '百炼'}</strong>
      {turn.reasoning && <Reasoning text={turn.reasoning} pending={!!turn.pending} />}
      {turn.answer && <p>{turn.answer}</p>}
      {turn.pending && <p className="chat-pending" role="status">
        {turn.answer ? '正在生成回复…' : turn.reasoning ? '正在推理…' : '正在处理消息…'}
      </p>}
    </div>
  </div>
}

function Reasoning({ text, pending }: { text: string; pending: boolean }) {
  const [open, setOpen] = useState(pending)
  return <div className="chat-reasoning">
    <button type="button" aria-expanded={open} onClick={() => setOpen(current => !current)}>
      {open ? '收起推理过程' : '展开推理过程'}
    </button>
    <p hidden={!open}>{text}</p>
  </div>
}
