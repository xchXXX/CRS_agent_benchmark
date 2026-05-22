import api from './api'

type ExcelCellValue = string | number | boolean | null | undefined
type ExcelRow = ExcelCellValue[] | { values: ExcelCellValue[]; style?: string }

function normalizeBenchmarkTrack(track: string) {
  const normalized = String(track || '').trim().toLowerCase().replace(/\s+/g, '')
  const aliases: Record<string, string> = {
    production_flow: 'production_flow',
    productionflow: 'production_flow',
    'production-flow': 'production_flow',
    full_chain: 'production_flow',
    fullchain: 'production_flow',
    real_flow: 'production_flow',
    realflow: 'production_flow',
    raw_retrieval: 'raw_retrieval',
    rawretrieval: 'raw_retrieval',
    'raw-retrieval': 'raw_retrieval',
    raw: 'raw_retrieval',
    final_list: 'final_list',
    finallist: 'final_list',
    'final-list': 'final_list',
    list: 'final_list',
  }
  return aliases[normalized] || track
}

function parseDownloadError(text: string, fallback: string) {
  try {
    return JSON.parse(text)?.detail || fallback
  } catch {
    return text || fallback
  }
}

function escapeXml(value: ExcelCellValue) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function excelCell(value: ExcelCellValue, style?: string) {
  const styleAttr = style ? ` ss:StyleID="${style}"` : ''
  if (typeof value === 'number' && Number.isFinite(value)) {
    return `<Cell${styleAttr}><Data ss:Type="Number">${value}</Data></Cell>`
  }
  if (typeof value === 'boolean') {
    return `<Cell${styleAttr}><Data ss:Type="String">${value ? '是' : '否'}</Data></Cell>`
  }
  return `<Cell${styleAttr}><Data ss:Type="String">${escapeXml(value)}</Data></Cell>`
}

function excelRow(row: ExcelRow, fallbackStyle?: string) {
  const values = Array.isArray(row) ? row : row.values
  const style = Array.isArray(row) ? fallbackStyle : row.style || fallbackStyle
  return `<Row>${values.map(value => excelCell(value, style)).join('')}</Row>`
}

function excelSheet(name: string, headers: string[] | null, rows: ExcelRow[], widths: number[] = []) {
  return `
    <Worksheet ss:Name="${escapeXml(name).slice(0, 31)}">
      <Table>
        ${widths.map(width => `<Column ss:Width="${width}"/>`).join('')}
        ${headers ? excelRow(headers, 'Header') : ''}
        ${rows.map(row => excelRow(row)).join('')}
      </Table>
      <WorksheetOptions xmlns="urn:schemas-microsoft-com:office:excel">
        <FreezePanes/>
        <FrozenNoSplit/>
        <SplitHorizontal>1</SplitHorizontal>
        <TopRowBottomPane>1</TopRowBottomPane>
      </WorksheetOptions>
    </Worksheet>
  `
}

function downloadBlob(blob: Blob, filename: string) {
  const blobUrl = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = blobUrl
  link.download = filename
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  setTimeout(() => {
    document.body.removeChild(link)
    window.URL.revokeObjectURL(blobUrl)
  }, 100)
}

function downloadReportFromDetail(runId: string, detail: BenchmarkRunDetailResponse) {
  const summary = detail.report?.summary || {}
  const predictions = detail.predictions || []
  const failures = detail.report?.failures || []
  const caseById = new Map(predictions.map(item => [item.case_id, item.case_snapshot || {}]))
  const pct = (value: any) => typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : '-'
  const fmt = (value: any) => {
    if (value === undefined || value === null || value === '') return '-'
    if (typeof value === 'boolean') return value ? '是' : '否'
    if (typeof value === 'object') return JSON.stringify(value)
    return String(value)
  }
  const hasRank = (value: unknown) => value !== undefined && value !== null && value !== ''
  const resultStatus = (item: BenchmarkPrediction) => {
    const caseSnapshot = caseById.get(item.case_id) || {}
    const goldAnswerable = caseSnapshot?.gold?.answerable
    if (item.error) return '执行错误'
    if (goldAnswerable === false) {
      const results = item.results_scored || item.results || []
      const validity = item.runtime?.validity || {}
      return (item.answerable === false || !results.length || validity.has_valid_results === false)
        ? '无资料正确'
        : '无资料误召回'
    }
    if (item.best_rank_in_top_k ?? item.best_rank) return '主榜命中'
    if (hasRank(item.best_rank_full)) return '主榜外召回'
    return '未召回'
  }
  const statusStyle = (status: string) => {
    if (['主榜命中', '无资料正确'].includes(status)) return 'Success'
    if (status === '主榜外召回') return 'Warning'
    if (['未召回', '无资料误召回', '执行错误'].includes(status)) return 'Danger'
    return undefined
  }
  const overviewRows: ExcelRow[] = [
    { values: ['资料搜索 Benchmark 测试报告', '', '', '', '', ''], style: 'Title' },
    { values: [`Run: ${detail.config?.run_id || detail.status?.run_id || runId}`, `Dataset: ${detail.config?.dataset_id || '-'}`, `Track: ${detail.config?.track || '-'}`, `主榜 Top-K: ${detail.config?.top_k || '-'}`, `状态: ${detail.status?.status || '-'}`, ''], style: 'Subtitle' },
    { values: ['关键指标', '', '', '', '', ''], style: 'Section' },
    { values: ['Recall@5', 'Recall@10', 'Recall@50', 'Recall@100', 'MRR', '诊断池未命中'], style: 'Header' },
    { values: [pct(summary.recall_at_5), pct(summary.recall_at_10), pct(summary.recall_at_50), pct(summary.recall_at_100), typeof summary.mrr === 'number' ? summary.mrr.toFixed(3) : '-', summary.not_found_in_pool_count ?? 0], style: 'MetricValue' },
    { values: ['目标进前5比例', '目标进前10比例', '目标进前50比例', '目标进前100比例', '排名越靠前越高', '完整池也未找到目标'], style: 'MetricNote' },
    { values: ['运行配置', '', '', '', '', ''], style: 'Section' },
    ['字段', '值', '说明', '', '', ''],
    ['状态', detail.status?.status, 'completed 表示本次评测已结束', '', '', ''],
    ['开始时间', detail.status?.started_at, '', '', '', ''],
    ['完成时间', detail.status?.finished_at, '', '', '', ''],
    ['总案例数', summary.total_cases, '包含可答案例和无资料案例', '', '', ''],
    ['可答案例', summary.answerable_cases, '有人工标注正确资料的 case', '', '', ''],
    ['无资料案例', summary.no_answer_cases, '人工标注不应返回资料的 case', '', '', ''],
    { values: ['指标解释', '', '', '', '', ''], style: 'Section' },
    ['Recall@K', `R@5=${pct(summary.recall_at_5)} / R@50=${pct(summary.recall_at_50)}`, '可答案例中，正确资料在诊断候选池中进入前 5/10/50/100 的比例。', '', '', ''],
    ['MRR', typeof summary.mrr === 'number' ? summary.mrr.toFixed(3) : '-', '正确资料排名倒数的平均值，越高越好。', '', '', ''],
    ['主榜中位名次', fmt(summary.median_gold_rank), '只统计主榜 Top-K 命中的 case，观察主榜排序集中位置。', '', '', ''],
    ['诊断池中位名次', fmt(summary.median_gold_rank_full), '在诊断候选池中找到目标资料时的中位排名。', '', '', ''],
    ['主榜外召回率', pct(summary.beyond_top_k_rate), '能在诊断候选池找到，但没有进入主榜 Top-K，通常是 rerank 问题。', '', '', ''],
    ['诊断池未命中率', pct(summary.not_found_in_pool_rate), '完整池也没有目标，通常是 query/索引/召回问题。', '', '', ''],
    ['No-answer Accuracy', pct(summary.no_answer_accuracy), '无资料样例被系统正确判断为无有效资料的比例。', '', '', ''],
  ]
  const taskRows = Object.entries(detail.report?.by_task_type || {}).map(([taskType, stats]: any) => [
    taskType,
    stats.total ?? '',
    pct(stats.recall_at_5),
    pct(stats.recall_at_10),
    pct(stats.recall_at_50),
    pct(stats.recall_at_100),
  ])
  const caseRows = predictions.map(item => {
    const scoredResults = item.results_scored || item.results || []
    const status = resultStatus(item)
    const imageCount = (item.image_paths || []).length || (item.image_evidence || []).length
    return {
      values: [
      item.case_id,
      status,
      item.question_text,
      imageCount,
      item.image_evidence_summary,
      item.effective_query,
      item.track,
      fmt(caseById.get(item.case_id)?.gold?.answerable ?? item.answerable),
      item.best_rank_in_top_k ?? item.best_rank ?? '',
      item.best_rank_full ?? '',
      fmt(item.hit_in_top_k),
      item.returned_result_count ?? scoredResults.length,
      item.full_result_count ?? item.results_full?.length ?? scoredResults.length,
      (item.matched_gold_names || []).join(' | '),
      (item.matched_result_doc_names || []).join(' | '),
      scoredResults.slice(0, 5).map(result => result.doc_name).join(' | '),
      item.runtime?.latency_ms ?? '',
      item.runtime?.response_type ?? '',
      item.runtime?.diagnostic_rank_source ?? '',
      item.error,
      ],
      style: statusStyle(status),
    }
  })
  const planRows = predictions.flatMap(item => {
    const plannedQueries = item.planned_queries || []
    if (!plannedQueries.length) {
      return [[item.case_id, '', '', '', '', item.effective_query || '']]
    }
    return plannedQueries.map((query, index) => [
      item.case_id,
      index + 1,
      query.query || '',
      query.confidence ?? '',
      query.hit_count ?? '',
      item.effective_query || '',
    ])
  })
  const imageRows = predictions.flatMap(item =>
    (item.image_evidence || []).map((evidence: any) => [
      item.case_id,
      evidence.image_evidence_id,
      evidence.scene,
      evidence.summary,
      JSON.stringify(evidence.vehicle || {}),
      JSON.stringify(evidence.diagnosis || {}),
      (evidence.visible_text || []).join(' | '),
      (evidence.suggested_queries || []).join(' | '),
      evidence.confidence,
      evidence.needs_user_confirm,
    ]),
  )
  const inputRows = predictions.map(item => [
    item.case_id,
    item.question_text || '',
    item.case_snapshot?.gold?.answerable ?? '',
    (item.case_snapshot?.gold?.acceptable_doc_names || []).join(' | '),
    (item.image_paths || []).join(' | '),
    JSON.stringify(item.image_inputs || []),
    JSON.stringify(item.case_snapshot || {}),
  ])
  const requestRows = predictions.map(item => [
    item.case_id,
    JSON.stringify(item.request_payload || {}),
    item.runtime?.response_type || '',
    item.runtime?.business || '',
    JSON.stringify(item.response_payload || {}),
    JSON.stringify(item.search_snapshot || {}),
    JSON.stringify(item.runtime || {}),
  ])
  const contextRows = predictions.map(item => [
    item.case_id,
    JSON.stringify(item.case_context_before || {}),
    JSON.stringify(item.case_context_after || {}),
  ])
  const traceRows = predictions.flatMap(item => {
    const traceEntries = item.trace_entries || []
    if (!traceEntries.length) {
      return [[item.case_id, '', '', '', '', '']]
    }
    return traceEntries.map((entry: any) => [
      item.case_id,
      entry.sequence_no ?? '',
      entry.event_type || '',
      entry.detail || '',
      entry.created_at || '',
      JSON.stringify(entry.payload || {}),
    ])
  })
  const fullResultRows = predictions.flatMap(item => {
    const fullResults = item.results_full || []
    if (!fullResults.length) {
      return [[item.case_id, '', '', '', '', '']]
    }
    return fullResults.map(result => [
      item.case_id,
      result.rank,
      result.doc_name,
      result.doc_id || '',
      result.score ?? '',
      result.path || '',
    ])
  })
  const failureRows = failures.map(item => [
    item.case_id,
    item.failure_type,
    item.question_text,
    item.gold_doc_names,
    item.best_rank_in_top_k,
    item.best_rank_full,
    item.matched_gold_names,
    item.matched_result_doc_names,
    item.top_results,
  ])
  const eventRows = (detail.events || []).map(item => [
    item.ts,
    item.event_type,
    item.message,
    item.payload?.case_id || '',
    item.payload ? JSON.stringify(item.payload) : '',
  ])
  const glossaryRows = [
    ['评测结论', 'Case明细', '主榜命中、主榜外召回、未召回、无资料正确、无资料误召回、执行错误。'],
    ['主榜名次', 'Case明细', '正确资料在主榜 Top-K 候选中的最好排名，空值表示主榜未命中。'],
    ['完整池名次', 'Case明细', '正确资料在诊断候选池中的最好排名，用于区分排序问题和召回问题。'],
    ['命中的Gold资料', 'Case明细', '标注为正确资料且被返回结果匹配到的资料名，支持多个正确资料。'],
    ['实际搜索问题', 'Case明细', '图片识别和 LLM 查询规划后，真正用于调用搜索接口的问题。'],
    ['图片识别摘要', 'Case明细/图片识别', '图片证据分析的摘要，便于排查图片是否被正确理解。'],
    ['案例输入', '案例输入', '完整保留 case 输入、gold、图片路径、图片输入元数据和 case 快照。'],
    ['请求响应', '请求响应', '保留 benchmark 构造的 ChatRequest、ChatResponse、搜索快照和 runtime。'],
    ['上下文快照', '上下文快照', '执行前后 case context 对比，用于看图片识别和搜索结果是否写入上下文。'],
    ['Trace明细', 'Trace明细', '逐条展开 case 内部 trace 事件，而不是只看全局执行日志。'],
    ['完整候选', '完整候选', '展开完整诊断候选池，用于判断是主榜裁切问题还是底层召回问题。'],
    ['Recall@K', '报告总览/按问题类型', '可答案例中，正确资料在诊断候选池中进入前 5/10/50/100 的比例。'],
    ['MRR', '报告总览', '正确资料排名倒数的平均值，越高越好。'],
    ['主榜外召回', '报告总览/失败样例', '诊断候选池能找到目标，但排序没有进入主榜 Top-K。'],
    ['诊断池未命中', '报告总览/失败样例', '扩大到诊断池后仍找不到目标，优先排查召回、索引或 query。'],
  ]
  const workbook = `<?xml version="1.0" encoding="UTF-8"?>
    <?mso-application progid="Excel.Sheet"?>
    <Workbook
      xmlns="urn:schemas-microsoft-com:office:spreadsheet"
      xmlns:o="urn:schemas-microsoft-com:office:office"
      xmlns:x="urn:schemas-microsoft-com:office:excel"
      xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"
      xmlns:html="http://www.w3.org/TR/REC-html40">
      <Styles>
        <Style ss:ID="Default" ss:Name="Normal">
          <Alignment ss:Vertical="Top" ss:WrapText="1"/>
          <Font ss:FontName="Helvetica Neue" ss:Size="11"/>
          <Borders>
            <Border ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="1" ss:Color="#D7DEE8"/>
          </Borders>
        </Style>
        <Style ss:ID="Title">
          <Alignment ss:Vertical="Center"/>
          <Font ss:FontName="Helvetica Neue" ss:Size="18" ss:Bold="1" ss:Color="#FFFFFF"/>
          <Interior ss:Color="#122033" ss:Pattern="Solid"/>
        </Style>
        <Style ss:ID="Subtitle">
          <Alignment ss:Vertical="Center" ss:WrapText="1"/>
          <Font ss:FontName="Helvetica Neue" ss:Size="11" ss:Color="#BFD8D0"/>
          <Interior ss:Color="#122033" ss:Pattern="Solid"/>
        </Style>
        <Style ss:ID="Header">
          <Alignment ss:Vertical="Center" ss:Horizontal="Center" ss:WrapText="1"/>
          <Font ss:FontName="Helvetica Neue" ss:Size="11" ss:Bold="1" ss:Color="#FFFFFF"/>
          <Interior ss:Color="#203044" ss:Pattern="Solid"/>
        </Style>
        <Style ss:ID="Section">
          <Alignment ss:Vertical="Center" ss:WrapText="1"/>
          <Font ss:FontName="Helvetica Neue" ss:Size="12" ss:Bold="1" ss:Color="#0F172A"/>
          <Interior ss:Color="#EAF7F3" ss:Pattern="Solid"/>
        </Style>
        <Style ss:ID="MetricValue">
          <Alignment ss:Vertical="Center" ss:Horizontal="Center" ss:WrapText="1"/>
          <Font ss:FontName="Helvetica Neue" ss:Size="15" ss:Bold="1" ss:Color="#0F172A"/>
          <Interior ss:Color="#EAF7F3" ss:Pattern="Solid"/>
        </Style>
        <Style ss:ID="MetricNote">
          <Alignment ss:Vertical="Center" ss:Horizontal="Center" ss:WrapText="1"/>
          <Font ss:FontName="Helvetica Neue" ss:Size="10" ss:Color="#64748B"/>
          <Interior ss:Color="#F3F6FA" ss:Pattern="Solid"/>
        </Style>
        <Style ss:ID="Success">
          <Interior ss:Color="#E8F7EF" ss:Pattern="Solid"/>
        </Style>
        <Style ss:ID="Warning">
          <Interior ss:Color="#FFF4DE" ss:Pattern="Solid"/>
        </Style>
        <Style ss:ID="Danger">
          <Interior ss:Color="#FDECEC" ss:Pattern="Solid"/>
        </Style>
      </Styles>
      ${excelSheet('报告总览', null, overviewRows, [150, 150, 260, 120, 120, 120])}
      ${excelSheet('按问题类型', ['问题类型', '样本数', 'Recall@5', 'Recall@10', 'Recall@50', 'Recall@100'], taskRows, [160, 80, 90, 90, 90, 90])}
      ${excelSheet('Case明细', [
        'Case ID',
        '评测结论',
        '用户问题',
        '图片数',
        '图片识别摘要',
        '实际搜索问题',
        '评测链路',
        '是否应命中',
        '主榜名次',
        '完整池名次',
        '主榜命中',
        '主榜结果数',
        '完整池结果数',
        '命中的Gold资料',
        '命中的返回资料',
        'Top 5结果',
        '耗时(ms)',
        '响应类型',
        '诊断来源',
        '错误',
      ], caseRows, [140, 110, 300, 70, 300, 280, 120, 90, 80, 90, 80, 90, 90, 220, 240, 360, 80, 120, 130, 220])}
      ${excelSheet('图片识别', [
        'Case ID',
        '图片证据ID',
        '场景',
        '识别摘要',
        '车辆信息',
        '诊断信息',
        '可见文字',
        '建议查询',
        '置信度',
        '是否需确认',
      ], imageRows, [140, 160, 120, 320, 260, 260, 300, 300, 90, 120])}
      ${excelSheet('查询规划', ['Case ID', '查询序号', '规划查询', '置信度', '命中数', '实际搜索问题'], planRows, [140, 90, 360, 90, 90, 360])}
      ${excelSheet('案例输入', ['Case ID', '用户问题', 'Gold是否可答', 'Gold资料', '图片路径', '图片输入元数据', 'Case快照'], inputRows, [140, 320, 90, 260, 260, 320, 420])}
      ${excelSheet('请求响应', ['Case ID', '请求载荷', '响应类型', '业务', '响应载荷', '搜索快照', 'Runtime'], requestRows, [140, 360, 120, 120, 420, 420, 360])}
      ${excelSheet('上下文快照', ['Case ID', '执行前Context', '执行后Context'], contextRows, [140, 420, 420])}
      ${excelSheet('Trace明细', ['Case ID', '序号', '事件类型', '详情', '时间', 'Payload'], traceRows, [140, 70, 180, 220, 180, 420])}
      ${excelSheet('完整候选', ['Case ID', '排名', '资料名', '文档ID', '分数', '路径'], fullResultRows, [140, 70, 360, 160, 100, 420])}
      ${excelSheet('失败样例', [
        'Case ID',
        '失败类型',
        '用户问题',
        'Gold资料',
        '主榜名次',
        '完整池名次',
        '命中的Gold资料',
        '命中的返回资料',
        'Top 结果',
      ], failureRows, [140, 140, 320, 240, 90, 90, 220, 240, 360])}
      ${excelSheet('执行日志', ['时间', '事件类型', '消息', 'Case ID', '事件载荷'], eventRows, [160, 180, 320, 140, 520])}
      ${excelSheet('字段说明', ['字段', '位置', '说明'], glossaryRows, [160, 180, 520])}
    </Workbook>`
  downloadBlob(
    new Blob([workbook], { type: 'application/vnd.ms-excel;charset=utf-8' }),
    `${runId}_report.xls`,
  )
}

export interface BenchmarkDataset {
  dataset_id: string
  case_count: number
  answerable_count: number
  no_answer_count: number
  path: string
  card: string
  updated_at: string | null
}

export interface BenchmarkRunSummary {
  run_id: string
  dataset_id: string
  track: string
  top_k: number
  created_by?: string | null
  created_at?: string | null
  status: string
  started_at?: string | null
  finished_at?: string | null
  summary?: {
    total_cases?: number
    answerable_cases?: number
    no_answer_cases?: number
    recall_at_5?: number
    recall_at_10?: number
    recall_at_50?: number
    recall_at_100?: number
    mrr?: number
    median_gold_rank?: number | null
    median_gold_rank_full?: number | null
    miss_rate?: number
    beyond_top_k_count?: number
    beyond_top_k_rate?: number
    not_found_in_pool_count?: number
    not_found_in_pool_rate?: number
    no_answer_accuracy?: number | null
  } | null
  progress?: {
    total: number
    completed: number
    failed: number
  }
  error?: string | null
}

export interface BenchmarkEvent {
  ts: string
  event_type: string
  message: string
  payload?: Record<string, any>
}

export interface BenchmarkPrediction {
  case_id: string
  track: string
  answerable: boolean
  question_text?: string
  case_snapshot?: Record<string, any>
  image_paths?: string[]
  image_inputs?: Array<Record<string, any>>
  image_evidence_summary?: string
  effective_query?: string
  planned_queries?: Array<{
    query?: string
    confidence?: number
    hit_count?: number
  }>
  best_rank: number | null
  best_rank_in_top_k?: number | null
  best_rank_full?: number | null
  hit_in_top_k?: boolean
  results: Array<{
    rank: number
    doc_name: string
    doc_id?: string | null
    score?: number | null
    path?: string | null
  }>
  results_scored?: Array<{
    rank: number
    doc_name: string
    doc_id?: string | null
    score?: number | null
    path?: string | null
  }>
  results_full?: Array<{
    rank: number
    doc_name: string
    doc_id?: string | null
    score?: number | null
    path?: string | null
  }>
  returned_result_count?: number
  full_result_count?: number
  matched_gold_names?: string[]
  matched_result_doc_names?: string[]
  matched_items?: Array<{
    rank?: number | null
    doc_name: string
    matched_gold_names: string[]
  }>
  image_evidence?: Array<Record<string, any>>
  request_payload?: Record<string, any>
  response_payload?: Record<string, any>
  search_snapshot?: Record<string, any>
  case_context_before?: Record<string, any>
  case_context_after?: Record<string, any>
  trace_entries?: Array<Record<string, any>>
  runtime?: Record<string, any>
  error?: string | null
}

export interface BenchmarkRunDetailResponse {
  status: BenchmarkRunSummary
  config: Record<string, any>
  report: {
    summary?: BenchmarkRunSummary['summary']
    by_task_type?: Record<string, any>
    failures?: Array<Record<string, any>>
  }
  events: BenchmarkEvent[]
  predictions: BenchmarkPrediction[]
}

export interface BenchmarkOverview {
  datasets: {
    count: number
    total_cases: number
  }
  runs: {
    count: number
    running_count: number
    completed_count: number
    failed_count: number
  }
  latest_run: BenchmarkRunSummary | null
  latest_metrics: {
    recall_at_5?: number | null
    recall_at_10?: number | null
    recall_at_50?: number | null
    recall_at_100?: number | null
    mrr?: number | null
    no_answer_accuracy?: number | null
  }
  scope: {
    primary: string
    images_used_by_runner: boolean
    clarification_in_main_score: boolean
    full_rank_pool_k?: number
    note: string
  }
}

export const benchmarksService = {
  getOverview: () => api.get<BenchmarkOverview>('/admin/benchmarks/doc-search/overview'),

  getDatasets: () => api.get<{ items: BenchmarkDataset[] }>('/admin/benchmarks/doc-search/datasets'),

  getRuns: () => api.get<{ items: BenchmarkRunSummary[] }>('/admin/benchmarks/doc-search/runs'),

  startRun: (data: { dataset_id: string; track: string; top_k: number }) =>
    api.post<{ run_id: string; status: string }>('/admin/benchmarks/doc-search/runs', {
      ...data,
      track: normalizeBenchmarkTrack(data.track),
    }),

  pauseRun: (runId: string) =>
    api.post<{ run_id: string; status: string; message?: string }>(`/admin/benchmarks/doc-search/runs/${runId}/pause`),

  resumeRun: (runId: string) =>
    api.post<{ run_id: string; status: string }>(`/admin/benchmarks/doc-search/runs/${runId}/resume`),

  getRunDetail: (runId: string) => api.get<BenchmarkRunDetailResponse>(`/admin/benchmarks/doc-search/runs/${runId}`),

  downloadFailures(runId: string) {
    const token = localStorage.getItem('token')
    const url = `/chat/api/admin/benchmarks/doc-search/runs/${runId}/failures.csv`
    return fetch(url, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${token}`,
      },
    })
      .then(response => {
        if (!response.ok) {
          return response.text().then(text => {
            let detail = text
            try {
              detail = JSON.parse(text)?.detail || text
            } catch {
              // Keep plain-text fallback.
            }
            throw new Error(detail || '下载失败')
          })
        }
        return response.blob()
      })
      .then(blob => {
        const blobUrl = window.URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = blobUrl
        link.download = `${runId}_failures.csv`
        link.style.display = 'none'
        document.body.appendChild(link)
        link.click()
        setTimeout(() => {
          document.body.removeChild(link)
          window.URL.revokeObjectURL(blobUrl)
        }, 100)
      })
  },

  downloadReport(runId: string, detail?: BenchmarkRunDetailResponse | null) {
    const token = localStorage.getItem('token')
    const url = `/chat/api/admin/benchmarks/doc-search/runs/${runId}/report.xlsx`
    return fetch(url, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${token}`,
      },
    })
      .then(response => {
        if (!response.ok) {
          return response.text().then(text => {
            const errorMessage = parseDownloadError(text, '导出失败')
            if (detail && (response.status === 404 || /not\s*found|notfound|不存在/i.test(errorMessage))) {
              downloadReportFromDetail(runId, detail)
              return null
            }
            throw new Error(errorMessage)
          })
        }
        return response.blob()
      })
      .then(blob => {
        if (!blob) {
          return { source: 'client_xls' as const }
        }
        const blobUrl = window.URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = blobUrl
        link.download = `${runId}_report.xlsx`
        link.style.display = 'none'
        document.body.appendChild(link)
        link.click()
        setTimeout(() => {
          document.body.removeChild(link)
          window.URL.revokeObjectURL(blobUrl)
        }, 100)
        return { source: 'server_xlsx' as const }
      })
  },
}
