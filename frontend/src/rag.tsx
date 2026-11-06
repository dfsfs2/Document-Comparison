import { useEffect, useRef, useState, type FormEvent } from 'react'

export type Rule = { id: string; title: string; rule: string; builtIn: boolean }
export type Draft = Pick<Rule, 'title' | 'rule'>
export type SearchResult = { matches: Rule[]; mode: string }

type Props = {
  load: () => Promise<Rule[]>
  add: (draft: Draft) => Promise<Rule>
  remove: (id: string) => Promise<void>
  retrieve: (query: string, useCloud: boolean) => Promise<SearchResult>
}

const searchModes: Record<string, string> = {
  vector: '百炼向量检索',
  keyword: '本地关键词检索',
  unconfigured: '本地关键词检索 · 百炼未配置',
  fallback: '本地关键词检索 · 向量请求失败',
  empty: '知识库为空',
}

export function RagPage({ load, add, remove, retrieve }: Props) {
  const [rules, setRules] = useState<Rule[] | null>(null)
  const [loadError, setLoadError] = useState('')
  const [filter, setFilter] = useState('')
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [queryPending, setQueryPending] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [queryError, setQueryError] = useState('')
  const [queryResult, setQueryResult] = useState<SearchResult | null>(null)
  const titleField = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let active = true
    load().then(entries => { if (active) setRules(entries) })
      .catch(error => { if (active) setLoadError(error.message) })
    return () => { active = false }
  }, [load])

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const values = new FormData(form)
    setPending(true)
    setError('')
    setNotice('')
    try {
      const saved = await add({ title: String(values.get('title')), rule: String(values.get('rule')) })
      setRules(current => current ? [saved, ...current] : current)
      setFilter('')
      setQueryResult(null)
      setQueryError('')
      setNotice('规则已保存，可以试检索。')
      form.reset()
    } catch (error) {
      setError((error as Error).message)
    } finally {
      setPending(false)
    }
  }

  async function deleteRule(id: string) {
    setPending(true)
    setError('')
    setNotice('')
    try {
      await remove(id)
      setRules(current => current?.filter(rule => rule.id !== id) ?? null)
      setPendingDelete(null)
      setQueryResult(null)
      setQueryError('')
      setNotice('规则已删除。')
    } catch (error) {
      setError((error as Error).message)
    } finally {
      setPending(false)
    }
  }

  async function testQuery(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setQueryPending(true)
    setQueryError('')
    setQueryResult(null)
    try {
      setQueryResult(await retrieve(String(form.get('query')), form.has('useCloud')))
    } catch (error) {
      setQueryError((error as Error).message)
    } finally {
      setQueryPending(false)
    }
  }

  if (!rules) return <section className="panel report-loading">
    <a className="text-button" href="#">← 返回文档套版</a>
    {loadError ? <p className="error" role="alert">{loadError}</p> : <p role="status">正在读取 RAG 知识库…</p>}
  </section>

  const busy = pending || queryPending
  const builtIn = rules.filter(rule => rule.builtIn).length
  const keyword = filter.trim().toLocaleLowerCase()
  const visibleRules = rules.filter(entry => (entry.title + ' ' + entry.rule).toLocaleLowerCase().includes(keyword))

  return <>
    <section className="hero rag-hero">
      <div className="eyebrow"><span /> 我的排版知识</div>
      <h1>RAG 知识库</h1>
      <p>保存你对标题、正文和文档结构的判断规则。开启百炼辅助时，套版会先从这里找相关规则。</p>
    </section>
    <section className="panel rag-guide" aria-labelledby="rag-guide">
      <div className="section-heading"><span className="step">01</span><h2 id="rag-guide">如何加入 RAG 规则</h2></div>
      <ol>
        <li><strong>给规则起名</strong><span>写明文档类型或用途，例如「技术报告的章节标题」。</span></li>
        <li><strong>写清判断条件</strong><span>例如「独立成段的 2.1 适用范围按二级标题处理，带完整说明的句子按正文处理」。</span></li>
        <li><strong>保存并验证</strong><span>用下方试检索查看命中的规则，套版时勾选「百炼辅助识别」。</span></li>
      </ol>
      <button className="quiet" onClick={() => titleField.current?.focus()}>去新增规则</button>
    </section>
    <div className="rag-columns">
      <section className="panel rag-library">
        <div className="section-heading"><span className="step">02</span><h2>我的规则</h2></div>
        <div className="report-stats">
          <div><strong>{rules.length}</strong><span>全部规则</span></div>
          <div><strong>{builtIn}</strong><span>预置规则</span></div>
          <div><strong>{rules.length - builtIn}</strong><span>自建规则</span></div>
        </div>
        <label className="rag-field">搜索已有规则
          <input type="search" placeholder="按名称或内容搜索" value={filter} onChange={event => setFilter(event.target.value)} />
        </label>
        {notice && <p className="rag-notice" role="status">{notice}</p>}
        {error && <p className="error" role="alert">{error}</p>}
        {visibleRules.length === 0 && <p className="empty">{rules.length ? '没有匹配的规则。' : '知识库为空，可以从新增规则开始。'}</p>}
        <ul className="rag-rules">{visibleRules.map(entry => <li key={entry.id}>
          <div className="rag-rule-heading">
            <h3>{entry.title}</h3>
            <span className="badge">{entry.builtIn ? '预置' : '自建'}</span>
            <button className="quiet" aria-label={`删除 ${entry.title}`} disabled={busy}
              onClick={() => { setPendingDelete(entry.id); setError('') }}>删除</button>
          </div>
          <p>{entry.rule}</p>
          {pendingDelete === entry.id && <div className="rag-confirm">
            <span>从你的知识库删除这条规则？</span>
            <button className="quiet" disabled={busy} onClick={() => setPendingDelete(null)}>取消</button>
            <button className="danger" disabled={busy} onClick={() => deleteRule(entry.id)}>确认删除</button>
          </div>}
        </li>)}</ul>
      </section>
      <div className="rag-sidebar">
        <section className="panel">
          <div className="section-heading"><span className="step">03</span><h2>新增规则</h2></div>
          <form className="rag-form" onSubmit={create}>
            <label className="rag-field">规则名称
              <input name="title" ref={titleField} required maxLength={80} placeholder="例如 技术报告的章节标题" />
            </label>
            <label className="rag-field">规则内容
              <textarea name="rule" required rows={4} maxLength={1000} placeholder="写出判断条件，最好附一个例子" />
            </label>
            <button className="primary" disabled={busy || rules.length >= 100}>保存规则</button>
            {rules.length >= 100 && <small>已达到 100 条规则上限。</small>}
          </form>
        </section>
        <section className="panel">
          <div className="section-heading"><span className="step">04</span><h2>试检索</h2></div>
          <form className="rag-form" onSubmit={testQuery}>
            <label className="rag-field">检索内容
              <input name="query" required maxLength={300} placeholder="例如 2.1 适用范围" />
            </label>
            <label className="rag-check"><input type="checkbox" name="useCloud" disabled={busy} />使用百炼向量检索</label>
            <small>开启后，查询内容和规则会发给百炼生成向量。</small>
            <button className="primary" disabled={busy}>{queryPending ? '正在检索…' : '检索规则'}</button>
          </form>
          {queryError && <p className="error" role="alert">{queryError}</p>}
          {queryResult && <div className="rag-matches" aria-live="polite">
            <strong>{searchModes[queryResult.mode] || queryResult.mode}</strong>
            {queryResult.matches.length === 0 ? <p>没有检索到相关规则。</p> : <ol>
              {queryResult.matches.map(entry => <li key={entry.id}><strong>{entry.title}</strong><p>{entry.rule}</p></li>)}
            </ol>}
          </div>}
        </section>
      </div>
    </div>
  </>
}
