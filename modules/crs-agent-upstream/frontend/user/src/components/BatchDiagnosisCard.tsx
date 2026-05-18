/**
 * 批量诊断报告卡片
 *
 * 显示 ECU 型号标题
 * 报告列表，每项显示状态按钮：「查看」(ready) / 「生成」(not_found) / 「生成中...」(generating)
 */

import { FileText, Eye, Sparkles } from 'lucide-react'
import type { BatchReportItem } from '@/types'

interface BatchDiagnosisCardProps {
  ecuModel: string
  reports: BatchReportItem[]
  onViewReport: (reportUrl: string) => void
  onGenerateReport: (faultCode: string) => void
}

export default function BatchDiagnosisCard({
  ecuModel,
  reports,
  onViewReport,
  onGenerateReport
}: BatchDiagnosisCardProps) {
  // 统计各状态数量
  const readyCount = reports.filter(r => r.state === 'ready').length
  const generatingCount = reports.filter(r => r.state === 'generating').length
  const notFoundCount = reports.filter(r => r.state === 'not_found').length

  return (
    <div className="batch-diagnosis-card">
      <div className="batch-diagnosis-header">
        <div className="batch-diagnosis-icon">
          <FileText size={20} />
        </div>
        <div className="batch-diagnosis-title">
          <span className="title-main">诊断报告</span>
          <span className="title-ecu">{ecuModel}</span>
        </div>
        <div className="batch-diagnosis-stats">
          {readyCount > 0 && <span className="stat-ready">{readyCount} 已就绪</span>}
          {generatingCount > 0 && <span className="stat-generating">{generatingCount} 生成中</span>}
          {notFoundCount > 0 && <span className="stat-pending">{notFoundCount} 待生成</span>}
        </div>
      </div>

      <div className="batch-report-list">
        {reports.map((report) => (
          <div
            key={report.fault_code}
            className={`batch-report-item state-${report.state}`}
          >
            <div className="report-code-info">
              <span className="report-fault-code">{report.fault_code}</span>
            </div>

            <div className="report-action">
              {report.state === 'ready' && report.report_url && (
                <button
                  type="button"
                  className="report-action-btn btn-view"
                  onClick={() => onViewReport(report.report_url!)}
                >
                  <Eye size={14} />
                  查看
                </button>
              )}
              {report.state === 'not_found' && (
                <button
                  type="button"
                  className="report-action-btn btn-generate"
                  onClick={() => onGenerateReport(report.fault_code)}
                >
                  <Sparkles size={14} />
                  生成
                </button>
              )}
              {report.state === 'generating' && (
                <button
                  type="button"
                  className="report-action-btn btn-generating"
                  disabled
                >
                  <span className="btn-spinner-small" />
                  生成中
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* 全部查看按钮（当有多个就绪报告时显示） */}
      {readyCount > 1 && (
        <div className="batch-view-all">
          <span className="view-all-hint">点击各报告的「查看」按钮查看详情</span>
        </div>
      )}
    </div>
  )
}
