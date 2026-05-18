# real_world_wecom_train 聊天证据摘录

> 用途：
> - 为 `real_world_wecom_train` 当前 train 样本提供人工核查依据
> - 明确区分“数据库群导出可回溯样本”和“xls 人工整理样本”
> - 只写当前已知证据，不补造不存在的消息 ID、`room_id` 或 `transcript`

> 实现同步（2026-05-15）：
> 当前 train 样本的用户线索主口径已切到 `known_items / uncertain_items`。
> 本文中旧的 `known_facts / uncertain_facts` 描述，应理解为“可被展平到新结构的兼容来源”。

## 当前口径

- 当前 train 共 13 条正例 case
- 其中 9 条可直接回溯到当前数据库导出的微信群消息
- 另外 4 条来自 `benchmark/data_source2/benchmark_excel_template.xlsx`
- 对来自 xls 的样本，允许作为人工聊天证据源迁入 train，但 `metadata.evidence_source` 必须明确写为 `xls_manual_chat_curation`
- 对来自数据库导出的样本，`metadata.evidence_source` 写为 `db_room_export`
- 全部样本都按“唯一答案”口径收紧
- `case_000002` 与 `case_000010` 按用户裁决，只保留推荐文档作为唯一答案

## 用户已知信息抽取规范

- `initial_user_message` 只能重写用户实际发送过的提问内容，不允许掺入客服答复、gold 标题或求解后的正式资料名
- `known_items` 只能来自两类证据：
  - 用户发送的文本消息
  - `question_images` 中可以直接稳定读出的文字或型号信息
- 同一 case 如果用户发送了多条补充消息，`known_items` 必须合并这些后续补充，不允许只保留首条开场消息
- 图片信息应尽量抽足，但只保留肉眼可直接稳定读出的内容；模糊、遮挡、推断出的车型归属、与最终 gold 对齐后的反推信息都不能写入
- 不允许把最终命中文档标题、标准答案中的型号、或客服回复中的检索结论倒灌回 `known_items`
- 当图片里能读到较具体的型号、编号、厂牌、系列名时，应优先写入 `known_items`；只有不稳定或与文本线索存在歧义时才降到 `uncertain_items`

## A. 数据库群导出可回溯样本

目标群：`room_id = wr7pwYBwAAs6cb-jRCgXfvd0JukHTeDw`

### real_train_0001

- opening message id: `125536`
- answer message id: `125611`
- opening: `老师博世878云内的电脑板供电模块有吗`
- 唯一标题：`云内D20_MD1CC878原理图【国六】【原厂图】【2021.6.24】`

### real_train_0002

- opening message id: `159483`
- answer message id: `159508`
- opening: `老师好，麻烦帮忙查一下  重汽汕德卡G7驾驶室升降电路图`
- 唯一标题：`重汽_汕德卡G7_HTEA1_(全发动机)整车电路图【NanoBCU】【MAN、潍柴_博世MD1-2.2泵-国五】【2024-02版】`

### real_train_0003

- opening message id: `168902`
- supplemental message id: `168925`
- answer message id: `169149`
- opening: `老师好，请问怎么查重汽豪沃国六中央集电盒针脚定义图、还有右前底盘线束针脚定义图`
- 补充车型：`TH7国六460马力曼机`
- 唯一标题：`重汽_HTE2.0-TH7/黄河_燃油车_整车电路图【MAN/潍柴_博世MD1-2.2泵】【国六】`

### real_train_0004

- opening message id: `221395`
- answer message id: `221522`
- opening: `老师好，请问这个电脑板有嘛，福田`
- 唯一标题：`福田时代_驭菱VQ1_LD-JH-WFG03D_独悬车型_ECU电路图【福田4W12-汽油】【国五】`

### real_train_0005

- opening message id: `252614`
- answer message id: `252755`
- opening: `这个保险盒图纸谁知道？`
- 唯一标题：`重汽_斯太尔D7B_保险丝继电器盒定义【黑色迷你_D10发动机_EDC17CV44】`

### real_train_0006

- opening message id: `298354`
- chatrecord message id: `298355`
- answer message id: `298647`
- opening: `黄老师  这几个车平台上个有电路图吗`
- 中间澄清：`只有6360的，其他整车图还没有`
- 唯一标题：`山东临工_E6360_电路图【新款】.pdf`
- 口径说明：当前数据库证据只支持“只有6360的”；最终唯一答案取黄县华发送的文件标题，但不把“新款”写回用户侧已知信息

### real_train_0007

- source_case_id: `case_000002`
- opening message id: `131467`
- answer message id: `131479`
- opening: `老师这个板子资料是哪个，带计量单元2线的云内发动机，找了好几个都不对`
- 唯一标题：`【推荐】国方MDD01【81/40针】`
- 口径说明：原 test 样本为多答案容忍；当前按用户裁决只保留推荐文档

### real_train_0008

- source_case_id: `case_000003`
- opening message id: `131653`
- answer message id: `131696`
- opening: `老师，请问三一55C挖机电路图有嘛`
- 唯一标题：`三一_SY55_SY60_SY65_SY75-9挖掘机_仪表显示器针脚定义`

### real_train_0009

- source_case_id: `case_000004`
- opening message id: `150602`
- answer message id: `150623`
- opening: `老师，麻烦帮忙找下国六红岩杰狮H6 BCM的针脚定义图`
- 唯一标题：`杰狮H6_杰虎H6_BCM_电路图【燃油 燃气_2.3m驾驶室】【国六】`

## B. xls 人工整理样本

来源文件：`benchmark/data_source2/benchmark_excel_template.xlsx`

说明：

- 这些样本经用户确认，来源是同事阅读聊天记录后的手工整理
- 当前数据库导出的这个群里不一定能定位到原始聊天
- 因此只保留 xls 中已经确认的提问、图片、资料师傅回复和正确资料名称
- xls 样本中的用户侧信息应严格限制在“xls 原始提问 + 图片可直接读出的信息”范围内
- 若样本被标记为 `image_parsing_required`，则用户侧字段只保留 xls 原始提问中的文字信息，不吸收图片信息；图片线索仅保留在 `question_images` 中供运行时解析
- 不伪造数据库消息 ID、群 id 或 `room_id`

### real_train_0010

- source_case_id: `case_000007`
- xls 提问：`老师这个整车电路图资料有吗，我看平台只有液压板的针脚`
- 图片可直接读出：`HYUNDAI`、`北京现代京城工程机械有限公司`、`R150LC-9`、`液压挖掘机`
- xls 正确资料名称：`现代_150LC-9挖掘机_液压电脑板针脚定义`
- 唯一标题：`现代_150LC-9挖掘机_液压电脑板针脚定义`

### real_train_0011

- source_case_id: `case_000008`
- xls 提问：`老师这个车的资料有吗`
- xls 资料师傅回复：`有个10的，参考下`
- 图片可直接读出：`HOWO`、`中国重型汽车集团有限公司`、`ZZ1257N4048W`
- xls 正确资料名称：`重汽豪泺_2010版ZZ1257N4048W_罐车线束图【豪沃】`
- 唯一标题：`重汽豪泺_2010版ZZ1257N4048W_罐车线束图【豪沃】`
- case 类型：`image_parsing_required`
- 口径说明：该样本专门用于测试图片解析；用户侧只保留原始提问和师傅回复中的文字信息，不把 `HOWO / 中国重型汽车集团有限公司 / ZZ1257N4048W` 写入 `known_facts`

### real_train_0012

- source_case_id: `case_000009`
- xls 提问：`这个电路图有没老师`
- 图片可直接读出：`康明斯系统`、`CUMMINS`
- xls 正确资料名称：`康明斯D2.5_CM2621_F162_原理图【国六】`
- 唯一标题：`康明斯D2.5_CM2621_F162_原理图【国六】`
- 口径说明：用户侧字段不保留 `D2.5`、`CM2621`、`F162`，因为当前图片证据只能稳定支持 `康明斯系统` 和 `CUMMINS` 标识

### real_train_0013

- source_case_id: `case_000010`
- xls 提问：`老师。麻烦查下这板子的资料`
- 图片可直接读出：`ECU`、`华夏龙晖(北京)汽车电子科技股份有限公司`、`Vagon`、`L0100220129A0`、`VA2001035`、`L0369010242A0`、`VA2000Q`、`VA20015`
- xls 正确资料名称：`【推荐】华夏龙晖73针_ECU电路图`
- 唯一标题：`【推荐】华夏龙晖73针_ECU电路图`
- case 类型：`image_parsing_required`
- 口径说明：该样本专门用于测试图片解析；用户侧只保留“查下这板子的资料”这一文字层信息，不把图片中的 ECU、公司名和编号写入 `known_facts`

## 明确剔除的情况

- `benchmark_excel_template.xlsx` 中资料师傅已明确回复“没有资料”的项
- 无法给出正确资料名称的项
- 同一条 case 仍存在多个并列答案且未被用户明确裁决收紧的项
- 当前数据库与 xls 都无法提供足够证据支持的项
