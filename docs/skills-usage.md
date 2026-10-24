# 账单处理 Skills 使用说明

生成日期：2026-10-23。已生成5个项目级技能，放置在本项目 `.agents/skills/`；未写入全局技能目录。文件与本地测试已验证，**尚未验证当前聊天是否已重新加载新技能列表，也没有用真实邮箱或账本试跑新技能**。

## 直接调用

| 技能 | 用途 | 示例 |
|---|---|---|
| `bank-mail-extract` | 读取银行原邮件并整理完整明细 | `使用 $bank-mail-extract 提取指定银行这个账期的邮件，只整理明细，不记账不删除。` |
| `bank-ledger-reconcile` | 一对一对账，给出差异及金额 | `使用 $bank-ledger-reconcile 对比广发指定月份完整账单和随手记，按持卡人分别列差异。` |
| `sui-ledger-entry` | 对授权清单查重、补录、重读核验 | `使用 $sui-ledger-entry 补录我确认的消费和退款，保留商户和日期来源，完成后逐笔核验。` |
| `verified-bank-mail-cleanup` | 复核固定候选清单，确认后仅移入垃圾箱 | `使用 $verified-bank-mail-cleanup 检查本轮完全匹配的日报，列明范围，执行前向我确认。` |
| `bank-bill-workflow` | 总流程入口 | `使用 $bank-bill-workflow 先对账，列出差异；没有我的补录或清理授权，不执行写入操作。` |

调用语句不能代替范围确认。建议明确银行、持卡人/卡尾号、交易/账单周期、账本以及所需动作。不要把“最近三天”与“三笔退款”混用。

## 项目现有规则

配置：`config/bill-policy.json`。

- 香港时区；根据本轮当前客户端日期解析相对日期。
- 汇丰日期匹配前后2个自然日，其他银行默认0天；这是已确认的本项目口径，不是通用银行事实。
- 同币种精确金额、一对一账目匹配，不用金额模糊容差。
- 退款沿用随手记既有“其他收入 → 退款”，记回原支付账户。
- 支出根据备注优先识别商家及用途：拼多多→日常用品（用户简称日用品）；中国移动/中移动→手机费；其余先匹配明确商家/用途对应的现有分类，找不到合适分类时统一→饮食其他。个人规则保存在配置的 `expense_classification`；不改变退款/还款类型，不自动修改已录账目。
- 账户映射表初始为空，运行时从实际卡尾号/持卡人/账本读取，不能仅凭同额猜映射。
- 记账备注只使用账单/交易记录原备注或摘要，不追加标识或解释；来源为空时留空。核验与防重元数据只放本地日志；新计划需记录完整的保存前实际ID快照，保存后以新增ID及完整字段核验。
- 默认只读；补录和清理是不同的操作边界。邮件清理只支持经确认移入垃圾箱。

技能仍可自动选择；没有因为涉及写入就全局关闭自动发现。但自动选择技能不等于自动获得写账或删除许可。

## 配套本地工具

都只处理本地JSON，没有浏览器、邮箱或银行接口调用。它们是核对/日志辅助，不是无人值守的记账或删除程序。

### 逐笔对账

```sh
python .agents/skills/bank-ledger-reconcile/scripts/reconcile.py runs/RUN_ID/input.json \
  --out runs/RUN_ID/report.json --markdown runs/RUN_ID/report.md
```

输入合同在该技能的 `references/input-contract.md`。金额用正的两位小数字符串；类型单独表示方向。原币/金额或日期不明时保留信息不足，不伪造零元交易。商户缺失、多个候选和同额笔数不足不会自动放行清理。

### 补录状态日志

```sh
python .agents/skills/sui-ledger-entry/scripts/entry_journal.py init PLAN.json JOURNAL.json
python .agents/skills/sui-ledger-entry/scripts/entry_journal.py submitted JOURNAL.json STABLE_KEY
python .agents/skills/sui-ledger-entry/scripts/entry_journal.py verify JOURNAL.json STABLE_KEY OBSERVED.json
```

`submitted`在真实保存前预占，不表示完成；稳定key只用于本地日志，不要求进入记账备注。无备注标识的新计划需要完整的`before_snapshot`；只有保存后唯一新增实际ID及字段一致才变`verified`。断连/超时先查询，不能重复新增。已知时分也纳入验证；来源只有日期时保留时间占位性质。

### 清理清单准备

```sh
python .agents/skills/verified-bank-mail-cleanup/scripts/prepare_cleanup.py \
  REPORT.json FRESH_SOURCES.json --out MANIFEST.json
```

要求重读邮件内容和整组消息库存；默认30分钟内的账目与来源证据。变化、过期、不完整、已有问题、只有部分交易匹配或原已在垃圾箱，都不作为本次新移入候选。输出始终不带执行授权，仍需实际执行前确认与操作后验证。

## 验证结果与边界

- 5个技能均通过内置 `quick_validate.py` 结构校验。
- 65项合成数据单元/CLI集成测试通过。
- 覆盖精确金额、币种隔离、退款与消费、还款转账、日期窗、同额多笔、全局一对一分配、来源变化与不完整、账目覆盖不足、写入结果不明、实际ID复用、已知时间误差及日志防重置。
- 未进行真实浏览器任务回放，没有新增任何真实账目、没有删除或移动任何真实邮件。
- 当前帮助能力仍依赖实际环境中可用的浏览器/邮箱工具；缺少登录或授权时停止说明，不读取隐藏接口或绕过安全检查。

在项目根目录运行本地测试：

```sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

## 数据与分发

`runs/`、`work/`和可选的`config/accounts.local.json`已列入 `.gitignore`。这只是版本控制忽略，不是加密或访问控制。

通用技能源码、测试和配置不含个人邮箱、真实银行卡号、历史邮件定位或银行实际明细。原对账历史数据没有自动搬进项目。

提供的zip只包含5个技能、项目配置、设计/使用说明、测试和忽略规则，不包含真实运行记录。解压或复制到别处会涉及目标项目的发现/配置范围，不能把这个项目的个人口径无条件推广过去。
