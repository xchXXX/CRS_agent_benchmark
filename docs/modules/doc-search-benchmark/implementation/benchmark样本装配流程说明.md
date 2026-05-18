# benchmark 样本装配流程说明

> 文档口径提示：
> 本文按
> [doc_search真实项目模糊用户模拟施工方案](../implement/engineering/doc_search真实项目模糊用户模拟施工方案.md)
> 的 `阶段 1` 口径说明运行时装配流程。
>
> 实现同步（2026-05-15）：
> 当前装配逻辑已支持 `known_items / uncertain_items`，并在缺失时从
> `known_facts / uncertain_facts` 自动展平出运行时线索视图。

## 1. 文档目的

本文说明 `fixture` 与 `gold` 如何装配成运行时 `TaskCase`，并解释：

- 新增 `user_profile`
- 新增 `target_doc`
- 如何保持老样本兼容

## 2. 装配入口

当前装配入口是：

- `benchmark/doc_search_bench/types.py`
- `merge_suite_from_paths()`

装配顺序固定为：

1. 读取 `fixture.json`
2. 读取 `gold.json`
3. 按 `case_id` 对齐
4. 合并成运行时 `TaskCase`

## 3. fixture 负责什么

`fixture` 负责运行输入与用户认知。

它至少承载：

- 用户问题
- 图片输入
- 首轮消息
- 多轮配置
- `user_simulation_config`

`阶段 1` 后新增可选承载：

- `user_profile`

当前 `user_profile` 的主线索字段为：

- `known_items`
- `uncertain_items`

## 4. gold 负责什么

`gold` 负责终点真值与评测目标。

它至少承载：

- `accepted_titles`
- `preferred_title`
- `expected_response_type`
- `page_goal_mode`
- `accepted_pages`
- `accepted_page_ranges`

`阶段 1` 后新增可选承载：

- `target_doc`

## 5. 当前推荐的运行时装配原则

### 5.1 保持增量扩展

`TaskCase` 在当前阶段应继续兼容旧字段，不因新字段缺失而失败。

### 5.2 用户认知与真值分层

装配后应保持：

- `user_profile` 属于模拟用户可消费认知
- `target_doc` 属于评测真值

不得因为装配方便，就把两者混在同一可见面里。

### 5.3 不引入路径真值

装配流程当前不应新增：

- `correct_path`
- `accepted_paths`
- `route_truth`

## 6. 兼容策略

当前第一批范围的兼容策略如下：

- 老 case 没有 `user_profile` 也可以运行
- 老 case 没有 `target_doc` 也可以运行
- 先只给少量 `dev/smoke` case 补齐新字段
- 既有 judge 仍基于 `accepted_titles` 工作

## 7. 当前建议的落盘结果

`merge_suite_from_paths()` 装配后，运行时对象至少要稳定拿到：

- `initial_user_message`
- `user_simulation_config`
- `user_profile`
- `accepted_titles`
- `target_doc`

当 `user_profile` 存在时，还应能稳定解析出：

- `resolve_known_items(profile)`
- `resolve_uncertain_items(profile)`

其中新增字段在本阶段全部允许为空。

## 8. 代码映射

- `benchmark/doc_search_bench/types.py`
  - 装配入口
- `benchmark/doc_search_bench/envs/doc_search/tasks_train.py`
  - train suite 注册入口
- `benchmark/doc_search_bench/utils/regenerate_train_from_xls.py`
  - 从 `资料树节点及关联文件原数据表.xls` 生成 train mock/synthetic 样本
- `benchmark/doc_search_bench/envs/doc_search/data/`
  - 样本目录

## 9. train 样本组织

train 样本按 case 类型组织，不按品牌组织。suite 名只表达“这组 case 在测什么”，品牌作为 case 内部事实优先保留在 `user_profile.known_items`，必要时兼容留在 `user_profile.known_facts.brand`。

当前 train suite：

- `low_information_opening`
  - 从资料源标题生成正样本
  - 首轮只给 1-2 个可见信息
  - `user_profile.known_items` 保留中等范围私有认知，但不暴露完整资料名、文件编号或页码
  - 用于验证低信息开场下的澄清与资料召回
- `vague_keyword_recall`
  - 从资料源标题生成正样本
  - 首轮给出中等范围信息，但资料类型或维修术语可能混用
  - 用于验证 ECU、针脚、CAN、整车电路等相近叫法混用时的澄清与资料召回
- `normal_informative_queries`
  - 从资料源标题生成正样本
  - 首轮给多个信息
  - 用于验证正常用户表达下的资料召回
- `image_parsing_required`
  - 图文正样本
  - 用户文字和 `user_profile.known_items` 只保留文字层已知信息，不预先注入图片中的可读型号或铭牌
  - 用于验证系统是否能通过 `question_images` 的解析结果补足关键线索并完成资料召回
- `synthetic_noise_queries`
  - 从正样本标题变异生成负样本
  - 用户私有认知也保持中等范围，但目标资料为空
  - 用于验证无目标资料时的处理

`merge_suite_from_paths()` 不关心 suite 内是否混合品牌，只按 `case_id` 合并 fixture/gold。拆分 `low_information_opening` 和 `vague_keyword_recall` 只改变 suite/file 组织，不需要改造 `TaskCase` 合同或装配代码。新增或重生成 train 样本时，应优先修改生成脚本和 `tasks_train.py`，避免手动维护按品牌文件。

## 10. 阶段 1 完成标准

满足以下条件即可视为 `阶段 1` 装配流程完成：

1. 老样本装配结果不受影响
2. 新样本装配后能拿到 `user_profile`
3. 新样本装配后能拿到 `target_doc`
4. 装配结果没有把 `target_doc` 暴露给模拟用户决策层
