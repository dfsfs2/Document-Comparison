import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { ChatPage, type ChatHistory, type ChatModels, type Turn } from './chat'
import { RagPage, type Rule, type SearchResult } from './rag'
import { modeNames, ReportPage, reportLink, type Job, type JobReport } from './report'
import './styles.css'

type Session = { username: string | null; csrfHeader: string; csrfToken: string }

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch('/api' + path, { ...options, cache: 'no-store' })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { message?: string } | null
    const fallback = response.status === 401 ? '账号或密码不正确，或登录已过期。'
      : response.status === 403 ? '会话校验失败，请刷新页面后重试。'
      : '请求失败，请稍后重试。'
    throw new Error(body?.message || fallback)
  }
  const body = await response.text()
  return (body ? JSON.parse(body) : undefined) as T
}

function mutate<T>(path: string, session: Session, body?: BodyInit, contentType?: string, method = 'POST') {
  const headers = new Headers({ [session.csrfHeader]: session.csrfToken })
  if (contentType) headers.set('Content-Type', contentType)
  return api<T>(path, { method, headers, body })
}

function readReportId(hash: string) {
  return /^#report\/([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$/i.exec(hash)?.[1]
}

function loadReport(id: string) {
  return api<JobReport>(`/jobs/${id}/report`)
}

function loadRules() {
  return api<Rule[]>('/knowledge')
}

function loadChat(model: string, before?: number) {
  const query = new URLSearchParams({ model })
  if (before !== undefined) query.set('before', String(before))
  return api<ChatHistory>(`/chat?${query}`)
}

function loadChatModels() {
  return api<ChatModels>('/chat/models')
}

function Icon({ type }: { type: 'logo' | 'doc' | 'arrow' | 'download' | 'history' | 'spark' }) {
  const paths: Record<typeof type, ReactNode> = {
    logo: <><path d="M8 7h10v13H8z"/><path d="M5 16H4V4h10v1M11 11h4m-4 4h4"/></>,
    doc: <><path d="M13 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V10z"/><path d="M13 3v7h7M8 14h8m-8 3h5"/></>,
    arrow: <path d="M4 12h15m-5-5 5 5-5 5"/>,
    download: <path d="M12 3v12m-4-4 4 4 4-4M4 16v4h16v-4"/>,
    history: <><path d="M3 12a9 9 0 1 1 2.6 6.4M3 6v6h6"/><path d="M12 7v6l4 2"/></>,
    spark: <path d="M12 2l2.5 7.5L22 12l-7.5 2.5L12 22l-2.5-7.5L2 12l7.5-2.5z"/>,
  }
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">{paths[type]}</svg>
}

function App() {
  const [session, setSession] = useState<Session | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [jobsError, setJobsError] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [register, setRegister] = useState(false)
  const [reference, setReference] = useState<File | null>(null)
  const [target, setTarget] = useState<File | null>(null)
  const [cloud, setCloud] = useState(false)
  const [hash, setHash] = useState(() => window.location.hash)
  const reportId = readReportId(hash)
  const showRag = hash === '#rag'
  const showChat = hash === '#chat'

  async function loadSession() {
    setSession(await api<Session>('/session'))
  }

  useEffect(() => {
    loadSession().catch(() => setError('无法连接应用服务，请确认后端已启动。'))
  }, [])

  useEffect(() => {
    const readHash = () => {
      setHash(window.location.hash)
      window.scrollTo(0, 0)
    }
    window.addEventListener('hashchange', readHash)
    return () => window.removeEventListener('hashchange', readHash)
  }, [])

  const username = session?.username
  useEffect(() => {
    if (!username) return
    let active = true
    setJobsError('')
    api<Job[]>('/jobs').then(data => { if (active) setJobs(data) })
      .catch(error => { if (active) setJobsError(error.message) })
    return () => { active = false }
  }, [username])

  async function authenticate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!session) return
    setBusy(true)
    setError('')
    const form = new FormData(event.currentTarget)
    const credentials = { username: String(form.get('username')), password: String(form.get('password')) }
    try {
      if (register) {
        await mutate<void>('/auth/register', session, JSON.stringify(credentials), 'application/json')
        setRegister(false)
      }
      await mutate<void>('/auth/login', session, new URLSearchParams(credentials))
      await loadSession()
    } catch (error) {
      setError((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function signOut() {
    if (!session) return
    setBusy(true)
    setError('')
    try {
      await mutate<void>('/auth/logout', session)
      setJobs([])
      setReference(null)
      setTarget(null)
      setCloud(false)
      setSession(null)
      await loadSession()
    } catch (error) {
      setError((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!reference || !target || !session) return
    setError('')
    for (const file of [reference, target]) {
      if (!/\.docx$/i.test(file.name) || file.size > 10 * 1024 * 1024 || file.size === 0) {
        setError('请上传两个非空的 .docx 文件，每个文件不超过 10 MB。')
        return
      }
    }
    setBusy(true)
    const data = new FormData()
    data.append('reference', reference)
    data.append('target', target)
    data.append('useCloud', String(cloud))
    try {
      const job = await mutate<Job>('/jobs', session, data)
      setJobs(previous => [job, ...previous].slice(0, 50))
      window.location.hash = reportLink(job.id)
    } catch (error) {
      setError((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return <>
    <header className="top"><div className="top-inner">
      <div className="brand"><span className="brand-mark"><Icon type="logo" /></span>文档对照<span className="top-label">WORD 工作台</span></div>
      {username && <nav className="top-nav" aria-label="工作台导航">
        <a href="#" aria-current={!showRag && !showChat ? 'page' : undefined}>文档套版</a>
        <a href="#rag" aria-current={showRag ? 'page' : undefined}>RAG 知识库</a>
        <a href="#chat" aria-current={showChat ? 'page' : undefined}>AI 聊天</a>
      </nav>}
      {username && <div className="account"><span>{username}</span><button className="quiet" onClick={signOut} disabled={busy}>退出登录</button></div>}
    </div></header>
    <main>
      {(!username || (!reportId && !showRag && !showChat)) && <section className="hero">
        <div className="eyebrow"><span /> 模板套版工作台</div>
        <h1>用一份文档，为另一份排版。</h1>
        <p>从参照文档提取样式，应用到目标文档。<br className="mobile-break" />保留目标原文、表格数据与图片。</p>
      </section>}
      {!username ? <section className="panel login">
        <div className="section-heading"><span className="step">01</span><h2>{register ? '创建账号' : '登录工作台'}</h2></div>
        <form onSubmit={authenticate}>
          <label>账号<input name="username" autoComplete="username" required pattern="[a-zA-Z0-9_]{3,32}" placeholder="3–32 位字母、数字或下划线" /></label>
          <label>密码<input type="password" name="password" autoComplete={register ? 'new-password' : 'current-password'}
            required minLength={register ? 8 : undefined} placeholder={register ? '至少 8 个字符' : '输入密码'} /></label>
          {error && <div className="error" role="alert">{error}</div>}
          <button className="primary" disabled={!session || busy}>{busy ? '处理中…' : register ? '注册并登录' : '登录'}<Icon type="arrow" /></button>
        </form>
        <button className="text-button" onClick={() => { setRegister(!register); setError('') }} disabled={busy}>
          {register ? '已有账号？返回登录' : '没有账号？创建账号'}
        </button>
      </section> : showChat && session ? <ChatPage key={username} load={loadChat} loadModels={loadChatModels}
        send={request => mutate<Turn>('/chat', session, JSON.stringify(request), 'application/json')} />
        : showRag && session ? <RagPage load={loadRules}
        add={draft => mutate<Rule>('/knowledge', session, JSON.stringify(draft), 'application/json')}
        extract={async file => {
          const data = new FormData()
          data.append('file', file)
          return (await mutate<{ text: string }>('/knowledge/extract', session, data)).text
        }}
        remove={id => mutate<void>(`/knowledge/${encodeURIComponent(id)}`, session, undefined, undefined, 'DELETE')}
        retrieve={(query, useCloud) => mutate<SearchResult>('/knowledge/query', session,
          JSON.stringify({ query, useCloud }), 'application/json')} />
        : reportId ? <ReportPage key={reportId} id={reportId} load={loadReport} /> : <>
        <section className="panel">
          <div className="section-heading"><span className="step">01</span><h2>选择文档</h2><span className="badge">DOCX</span></div>
          <form onSubmit={submit}>
            <div className="uploads">
              <FileInput title="参照文档" subtitle="提供你想沿用的排版样式" file={reference} disabled={busy} onChange={setReference} />
              <div className="upload-arrow"><Icon type="arrow" /></div>
              <FileInput title="目标文档" subtitle="保留内容，应用参照文档的排版" file={target} disabled={busy} onChange={setTarget} />
            </div>
            <label className="cloud">
              <span className="cloud-icon"><Icon type="spark" /></span>
              <span><strong>百炼辅助识别</strong><small>开启后，少量短段落会发往阿里云百炼，用于识别标题与正文。</small></span>
              <input type="checkbox" checked={cloud} onChange={event => setCloud(event.target.checked)} disabled={busy} />
            </label>
            {error && <div className="error" role="alert">{error}</div>}
            <div className="actions"><span>每份文件最大 10 MB</span><button className="primary" disabled={!reference || !target || busy}>
              {busy ? '正在套用格式…' : '开始套版'}<Icon type="arrow" /></button></div>
          </form>
        </section>
        <section className="panel history">
          <div className="section-heading"><span className="step">02</span><h2>套版记录</h2><span className="history-icon"><Icon type="history" /></span></div>
          {jobsError && <p className="error" role="alert">{jobsError}</p>}
          {jobs.length === 0 && !jobsError ? <p className="empty">完成首次套版后，生成的文档会显示在这里。</p> :
            <ul className="jobs">{jobs.map(job => <li key={job.id}>
              <span className="job-icon"><Icon type="doc" /></span>
              <div className="job-title"><strong title={job.targetName}>{job.targetName}</strong>
                <small title={job.referenceName}>参照 {job.referenceName} · {modeNames[job.mode] || job.mode}</small>
              </div>
              <time dateTime={job.createdAt}>{new Date(job.createdAt).toLocaleString('zh-CN', { hour12: false })}</time>
              {job.hasReport && <a className="quiet" href={reportLink(job.id)}>查看报告</a>}
              <a className="quiet" href={`/api/jobs/${job.id}/download`} aria-label={`下载 ${job.targetName}`}><Icon type="download" /></a>
            </li>)}</ul>}
        </section>
      </>}
      <footer>文档对照 <span>·</span> Word 排版工作台</footer>
    </main>
  </>
}

function FileInput({ title, subtitle, file, disabled, onChange }: {
  title: string; subtitle: string; file: File | null; disabled: boolean; onChange: (file: File | null) => void
}) {
  return <label className={file ? 'upload selected' : 'upload'}>
    <input type="file" accept=".docx" disabled={disabled} onChange={event => onChange(event.target.files?.[0] || null)} />
    <span className="upload-icon"><Icon type="doc" /></span>
    <strong>{title}</strong><span>{subtitle}</span>
    <span className="file-name" title={file?.name}>{file ? file.name : '选择 .docx 文件'}</span>
  </label>
}

createRoot(document.getElementById('root')!).render(<App />)
