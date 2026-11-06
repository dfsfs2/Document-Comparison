import { useEffect, useState } from 'react'

export type Job = {
  id: string; referenceName: string; targetName: string; paragraphs: number; tables: number
  mode: string; rules: string[]; createdAt: string; hasReport: boolean
}

type Kind = 'paragraph' | 'table' | 'section'
type Change = { property: string; before: string; after: string; affectedRuns?: number }
type Location = { kind: Kind; index: number; excerpt: string; reason: string; changes: Change[] }
export type JobReport = { job: Job; report: { counts: Record<Kind, number>; locations: Location[] } }

const names: Record<Kind, string> = { paragraph: '段落', table: '表格', section: '页面设置' }
const kinds = Object.keys(names) as Kind[]
const pageSize = 25

export const modeNames: Record<string, string> = {
  local: '本地规则', bailian: '百炼辅助', mixed: '百炼与本地规则',
  unconfigured: '本地规则 · 百炼未配置', fallback: '本地规则 · 百炼调用失败',
}

export function reportLink(id: string) {
  return `#report/${id}`
}

export function ReportPage({ id, load }: { id: string; load: (id: string) => Promise<JobReport> }) {
  const [data, setData] = useState<JobReport | null>(null)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState<Kind | 'all'>('all')
  const [page, setPage] = useState(1)

  useEffect(() => {
    let current = true
    load(id).then(result => { if (current) setData(result) })
      .catch(error => { if (current) setError(error.message) })
    return () => { current = false }
  }, [id, load])

  if (!data) return <section className="panel report-loading">
    <a className="text-button" href="#">← 返回工作台</a>
    {error ? <p className="error" role="alert">{error}</p> : <p role="status">正在读取套版报告…</p>}
  </section>

  const { job, report } = data
  const total = kinds.reduce((sum, kind) => sum + report.counts[kind], 0)
  const locations = report.locations.filter(location => filter === 'all' || location.kind === filter)
  const available = filter === 'all' ? total : report.counts[filter]
  const pageCount = Math.ceil(locations.length / pageSize)

  return <>
    <section className="report-hero">
      <a className="text-button" href="#">← 返回工作台</a>
      <div className="report-heading">
        <div>
          <div className="eyebrow"><span /> 套版完成 · {modeNames[job.mode] || job.mode}</div>
          <h1>套版报告</h1>
          <p className="report-file"><strong>目标文档</strong> {job.targetName}</p>
          <p className="report-file"><strong>参照文档</strong> {job.referenceName}</p>
        </div>
        <a className="primary" href={`/api/jobs/${id}/download`}>下载 Word 文档</a>
      </div>
    </section>
    <section className="panel">
      <div className="section-heading"><span className="step">01</span><h2>格式变更</h2></div>
      <div className="report-stats">{kinds.map(kind => <div key={kind}>
        <strong>{report.counts[kind]}</strong><span>{names[kind]}改动</span>
      </div>)}</div>
      {total === 0 ? <p className="empty">这次套版没有检测到所比较的格式属性发生变化。你仍可下载生成文档。</p> : <>
        <p className="report-note">段落、表格和节分别按各自在文档中的顺序编号。报告列出格式属性的前后值和所用的参照样式。</p>
        <div className="report-filters" role="group" aria-label="筛选变更类型">
          {(['all', ...kinds] as const).map(kind => <button className="quiet" key={kind}
            aria-pressed={filter === kind} onClick={() => { setFilter(kind); setPage(1) }}>
            {kind === 'all' ? '全部' : names[kind]}
          </button>)}
        </div>
        {locations.length < available && <p className="report-note">此分类记录了 {locations.length} 处明细，共检测到 {available} 处改动。</p>}
        {locations.length === 0 && available === 0 && <p className="empty">此分类没有改动。</p>}
        <ol className="report-changes">
          {locations.slice((page - 1) * pageSize, page * pageSize).map(location =>
            <li key={`${location.kind}-${location.index}`}>
              <h3>{location.kind === 'section' ? `第 ${location.index} 节 · 页面设置`
                : `第 ${location.index} 个${names[location.kind]}`}</h3>
              {location.excerpt && <blockquote>{location.excerpt}</blockquote>}
              <p className="report-reason"><strong>修改原因</strong> {location.reason}</p>
              <div className="report-diffs">{location.changes.map(change =>
                <div className="report-diff" key={change.property}>
                  <strong>{change.property}{change.affectedRuns !== undefined &&
                    <small>{change.affectedRuns} 个文字片段</small>}</strong>
                  <div><small>套版前</small>{change.before}</div>
                  <div><small>套版后</small>{change.after}</div>
                </div>)}</div>
            </li>)}
        </ol>
        {pageCount > 1 && <div className="report-pagination">
          <button className="quiet" disabled={page === 1} onClick={() => setPage(page - 1)}>上一页</button>
          <span aria-live="polite">第 {page} / {pageCount} 页</span>
          <button className="quiet" disabled={page === pageCount} onClick={() => setPage(page + 1)}>下一页</button>
        </div>}
      </>}
    </section>
  </>
}
