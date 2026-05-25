# 多目标文档 benchmark 改造方案

## 1. 文档目的

本文用于冻结 `doc_search benchmark` 从“单目标文档判定”升级为“多目标文档判定”的整体方案。

本方案只描述 benchmark 侧改造，不涉及任何前后端业务代码实现。

## 2. 背景

当前 benchmark 的主口径默认一个 case 只有一个目标文档，核心表现为：

- `gold` 侧只有单个 `target_doc`
- 文件评测主线围绕 `accepted_titles` 与单个 `matched_rank`
- 报告侧只有 `target_doc_file_id`、`target_doc_title`
- HTML review 默认展示单个目标标题

这套模型在“一个问题可能对应多个合法目标文档”的场景下会出现三个问题：

1. 无法表达“多个答案都算对”
2. 无法表达“必须覆盖多个目标才算对”
3. 无法稳定解释“命中了部分目标但未覆盖全部目标”的失败

## 3. 改造目标

本次改造目标如下：

- 让单个 case 支持 `0..N` 个目标文档真值
- 明确区分多目标判定策略
- 让运行、评测、报告、复盘视图都能消费多目标语义
- 保持对现有单目标样本的兼容读取

## 4. 非目标

以下内容不属于本次改造范围：

- 不修改被测 CRS 前后端代码
- 不新增具体业务 case 内容
- 不把页码 shadow gate 直接升级为 official gate
- 不重做 benchmark 目录结构

## 5. 总体设计

### 5.1 核心思路

把 case 真值从“单目标文档”升级为“目标文档集合 + 判定策略”。

建议新增：

- `target_docs`
- `target_match_mode`

其中：

- `target_docs`
  - 表示该 case 的全部合法目标文档
- `target_match_mode`
  - 表示命中规则

### 5.2 推荐判定策略

建议冻结两类策略：

- `any_of`
  - 命中任意一个目标文档即可通过
- `all_of`
  - 必须命中全部目标文档才通过

默认建议使用：

- `any_of`

原因是多数“目标不唯一”场景本质上是“这些文档中任意一个都可接受”，而不是“必须全部召回”。

## 6. V2 样本合同

### 6.1 gold 侧建议结构

建议将 `gold` 侧升级为如下结构：

```json
{
  "case_id": "case_000001",
  "target_match_mode": "any_of",
  "target_docs": [
    {
      "file_id": "doc_001",
      "title": "三一_SY55_SY60_SY65_SY75-9挖掘机_仪表显示器针脚定义",
      "doc_path": "三一/挖机/SY55/仪表显示器针脚定义",
      "facets": {
        "brand": "三一",
        "series": "SY",
        "model": "55C",
        "doc_type": "仪表针脚图"
      },
      "accepted_pages": [],
      "accepted_page_ranges": []
    },
    {
      "file_id": "doc_002",
      "title": "三一_SY55仪表针脚定义_补充版",
      "doc_path": "三一/挖机/SY55/仪表针脚定义/补充版",
      "facets": {
        "brand": "三一",
        "series": "SY",
        "model": "55C",
        "doc_type": "仪表针脚图"
      },
      "accepted_pages": [],
      "accepted_page_ranges": []
    }
  ],
  "expected_response_type": "documents"
}
```

### 6.2 兼容策略

当前保留但不属于页级/坐标级主真值的字段：

- `accepted_titles`
- `preferred_title`

当前读取策略冻结如下：

1. gold 只按 `target_docs` 读取正式真值
2. 不再从 `target_doc + accepted_titles` 回退构造单元素 `target_docs`
3. 若未显式提供 `target_match_mode`，默认使用 `any_of`

### 6.3 fixture 侧变化

`fixture` 侧的用户认知模型不需要因为多目标而大改。

本次改造重点仍在 `gold` 侧，因为“目标不唯一”属于评测真值问题，不是用户输入模型问题。

## 7. 类型系统改造方向

### 7.1 TaskCase

当前 `TaskCase` 需要从单目标模型升级为多目标模型。

建议新增或替换：

- `target_docs: list[TargetDocumentTruth]`
- `target_match_mode: str`

当前阶段：

- `target_doc` 已从 gold 主结构中废弃

### 7.2 TaskMetadataRecord

当前报告元数据只有：

- `target_doc_file_id`
- `target_doc_title`

建议扩展为：

- `target_doc_count`
- `target_doc_ids`
- `target_doc_titles`
- `target_match_mode`

旧字段兼容期可保留，但语义应改为“首个目标文档快照”，不能再被视为完整真值。

## 8. 评测语义改造

### 8.1 文件级判定

文件级判定不再只寻找一个 `matched_rank`，而是先计算：

- `matched_targets`
- `missed_targets`
- `matched_target_count`
- `target_coverage_rate`

再根据 `target_match_mode` 得出最终 verdict：

- `any_of`
  - `matched_target_count >= 1` 则通过
- `all_of`
  - `matched_target_count == target_doc_count` 才通过

### 8.2 排序指标

现有指标建议保留：

- `Recall@K`
- `Hit@1`
- `Hit@3`
- `MRR`

多目标下的计算建议：

- `Hit@1 / Hit@3 / MRR`
  - 基于“所有合法目标中的最佳命中 rank”
- `target_coverage_rate`
  - 单独反映多目标覆盖度

这样可以同时回答两个问题：

1. 系统是否足够快地召回到一个合法答案
2. 系统是否完整覆盖了全部目标

### 8.3 建议新增失败语义

建议新增或显式区分以下失败状态：

- `TARGET_SET_INCOMPLETE`
  - `all_of` 场景下只覆盖了部分目标
- `MULTI_TARGET_PARTIAL_HIT`
  - 命中了部分目标，但最终未达到 case 判定要求

说明：

- 若 `any_of` 已通过，则“只命中部分目标”不应视为正式失败
- 若 `all_of` 未覆盖全量，则应视为正式失败

## 9. 页码真值改造

### 9.1 当前问题

现有页码字段是 case 级：

- `accepted_pages`
- `accepted_page_ranges`

多目标后，这两个字段不再具备稳定语义，因为不同目标文档可能对应不同页码。

### 9.2 新口径

页码真值应绑定到具体目标文档，即放入：

- `target_docs[i].accepted_pages`
- `target_docs[i].accepted_page_ranges`

### 9.3 阶段性策略

在 case 数据未补齐前，页码继续保持：

- `shadow`

不建议在本次多目标改造完成时同步把页码升级到新的 official gate。

## 10. 报告与复盘改造

### 10.1 标准报告

标准报告建议新增：

- `target_match_mode`
- `target_doc_count`
- `matched_targets`
- `missed_targets`
- `matched_target_count`
- `target_coverage_rate`
- `all_targets_hit`
- `best_target_rank`

### 10.2 case rollup

`case_rollups` 与 suite summary 需要能回答：

- 多次 attempt 中是否至少覆盖过任一合法目标
- 多次 attempt 中是否出现过全覆盖
- `all_of` 场景下稳定性是否达标

### 10.3 HTML review

review 页面不能再只显示：

- `accepted_titles[0]`
- `target_doc_title`

必须升级为：

- 中文字段名
- 标准答案文档
- 实际返回文档
- 页级结果摘要
- 坐标级结果摘要
- 聊天轨迹
- 原始返回

## 11. 运行链路改造

本次运行器主逻辑不需要改变会话驱动方式，但需要让多目标真值贯穿以下环节：

- case 加载
- judge 判定
- actual report
- score report
- HTML review
- failure summary

也就是说，运行器改造重点不在请求协议，而在“结果收口与报告结构”。

## 12. 迁移策略

建议分四步迁移：

### 12.1 阶段 A：双格式兼容

- 该阶段已结束
- benchmark 内核不再以 `target_doc` 为 gold 真值入口

### 12.2 阶段 B：先迁移 train / dev

- 先在可调试数据上验证多目标逻辑
- 修正 judge、report、review 的展示问题

### 12.3 阶段 C：迁移 test

- test 样本迁移后再确认 official gate

### 12.4 阶段 D：清理旧主路径

- 旧 gold 主路径已切除
- `target_doc` 不再作为样本合同入口

## 13. 风险与控制

### 13.1 主要风险

- 只改数据结构，不改 judge 语义，导致“看起来支持多目标，实际仍按单目标判分”
- 文件命中与页码真值绑定错位
- 旧样本若仍带 `target_doc` 或 case 级页字段，将不再满足当前合同
- report 与 review 仍按单目标展示，导致人工复盘混乱

### 13.2 控制措施

- 先冻结 `target_match_mode`
- 不再保留 V1/V2 双读
- 页码继续 shadow，避免一次性扩大变更面
- report / HTML review 与 judge 同步改造，不允许只改一半

## 14. 冻结结论

本次 benchmark 多目标改造的核心结论如下：

- case 真值从单个 `target_doc` 升级为 `target_docs`
- 新增 `target_match_mode`，至少支持 `any_of / all_of`
- 评测主线从“单目标 rank 命中”升级为“目标集合覆盖 + 最佳 rank”
- 页码真值下沉到目标文档维度
- 运行链路尽量保持不变，重点改类型、judge、报告与 review
- 迁移顺序固定为“先双兼容，再迁移样本，最后切 gate”

