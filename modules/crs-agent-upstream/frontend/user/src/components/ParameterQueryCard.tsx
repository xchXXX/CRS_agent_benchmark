import type { ParameterQueryContent } from '../types'

interface ParameterQueryCardProps {
  content: ParameterQueryContent
  onOpenSources?: () => void
}

function shouldShowConnectorChip(content: ParameterQueryContent) {
  return content.requested_field !== 'connector_pin_no'
}

function shouldShowPinChip(content: ParameterQueryContent) {
  return content.requested_field !== 'ecu_pin_no'
}

function shouldShowVoltageChip(content: ParameterQueryContent, field: 'open_voltage' | 'static_voltage' | 'idle_voltage') {
  return content.requested_field !== field
}

export default function ParameterQueryCard({ content, onOpenSources }: ParameterQueryCardProps) {
  const rows = content.rows || []
  const sourceRefs = Array.isArray(content.source_refs) ? content.source_refs : []
  const ecuDisplay = [
    content.selected_source.ecu_name || content.selected_source.title,
    content.selected_source.system_voltage ? `${content.selected_source.system_voltage}V` : null,
  ].filter(Boolean).join(' · ')
  const showHeader = rows.length !== 1

  return (
    <div className="param-query-card">
      {showHeader && (
        <div className="param-query-header">
          <div className="param-query-summary">{content.summary}</div>
          <div className="param-query-source">{ecuDisplay}</div>
        </div>
      )}

      <div className="param-query-row-list">
        {rows.map((row) => {
          const rowName = row.component_name || row.ecu_pin_no || `第 ${row.row_no} 行`
          const rowNameLabel = row.component_name ? '针脚名称' : (row.ecu_pin_no ? '针脚编号' : '目标项')
          return (
            <div key={row.id} className="param-query-row">
              <div className="param-query-info-stack">
                <div className="param-query-info-block">
                  <div className="param-query-info-label">{rowNameLabel}</div>
                  <div className="param-query-info-value param-query-info-value--primary">{rowName}</div>
                </div>

                <div className="param-query-info-block">
                  <div className="param-query-info-label">ECU名称</div>
                  <div className="param-query-info-value">{ecuDisplay}</div>
                </div>
              </div>

              {row.requested_value && (
                <div className="param-query-primary-block">
                  <div className="param-query-primary-label">{content.requested_field_label || '结果信息'}</div>
                  <div className="param-query-primary-value">{row.requested_value}</div>
                </div>
              )}

              <div className="param-query-field-grid">
                {row.ecu_pin_no && row.component_name && shouldShowPinChip(content) && (
                  <span className="param-query-field-chip">ECU {row.ecu_pin_no}</span>
                )}
                {row.connector_pin_no && shouldShowConnectorChip(content) && (
                  <span className="param-query-field-chip">插件 {row.connector_pin_no}</span>
                )}
                {row.pin_definition && content.requested_field !== 'pin_definition' && (
                  <span className="param-query-field-chip">定义 {row.pin_definition}</span>
                )}
                {row.open_voltage_text && shouldShowVoltageChip(content, 'open_voltage') && (
                  <span className="param-query-field-chip">开路 {row.open_voltage_text}</span>
                )}
                {row.static_voltage_text && shouldShowVoltageChip(content, 'static_voltage') && (
                  <span className="param-query-field-chip">静态 {row.static_voltage_text}</span>
                )}
                {row.idle_voltage_text && shouldShowVoltageChip(content, 'idle_voltage') && (
                  <span className="param-query-field-chip">怠速 {row.idle_voltage_text}</span>
                )}
              </div>
              {row.remark && (
                <div className="param-query-remark">{row.remark}</div>
              )}
            </div>
          )
        })}
      </div>

      {sourceRefs.length > 0 && onOpenSources && (
        <div className="assistant-source-row">
          <button
            type="button"
            className="assistant-source-trigger"
            onClick={onOpenSources}
          >
            参考参数资料
            <span className="assistant-source-count">{sourceRefs.length}</span>
          </button>
        </div>
      )}
    </div>
  )
}
