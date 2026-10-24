---
name: sui-ledger-entry
description: 将用户已授权的缺失消费、退款、费用或还款查重后录入随手记，并重读实际记录验证；适用于补账请求，不负责真实付款、自动更改旧账或邮件清理。
---

# 随手记补录与核验

## 写入前

1. 明确目标账本、银行账户映射、交易类型与日期范围。仅请求“对账、给差异”时不执行本技能的写入部分。
2. 查看当前表单，已有金额/备注等草稿时先保护，不覆盖用户未保存内容。
3. 重读最新账目查重，包含收入、支出与转账。账户不一致或可能重复时保留待确认，不能擅改原记录或再新增。
4. 采用[随手记录入规则](references/sui-workflow.md)，把用户授权范围内的退款、费用、还款分别处理；不要默默只做消费。
5. 本地日志辅助工具的格式及状态见[journal-contract.md](references/journal-contract.md)。它不操作网页，也不证明已获得授权。

## 执行

- 稳定 `key`、来源引用和时间占位说明仅保存在本地日志；记账备注遵循项目 `ledger_memo`，只使用来源原备注，不追加这些核验信息。分类按[随手记录入规则](references/sui-workflow.md)读取项目已确认配置：优先从备注识别商家及用途，先用商户规则，再用明确用途和用户指定的兜底；分类标签不等于已证实交易用途。
- 保存前核验账户、类型、金额、日期、备注；在点击保存前将日志预占为 `submitted`，这样断连也不能盲目重试。这个状态不表示实际已保存。
- 保存结果不确定时设 `uncertain`，通过本地日志、保存前后实际ID差集及新鲜完整的账目检索恢复；禁止盲点保存或重新新增。
- 重新读取实际账目，以实际ID、数量和字段一致为完成证据。不能仅凭输入框清空/成功Toast声称保存成功。
- 只更改授权清单。真正支付、还信用卡、撤销真实交易与“记一条账”不同，不触碰银行付款控件。

## 日志命令

```sh
python .agents/skills/sui-ledger-entry/scripts/entry_journal.py init PLAN.json JOURNAL.json
python .agents/skills/sui-ledger-entry/scripts/entry_journal.py submitted JOURNAL.json KEY
python .agents/skills/sui-ledger-entry/scripts/entry_journal.py uncertain JOURNAL.json KEY
python .agents/skills/sui-ledger-entry/scripts/entry_journal.py verify JOURNAL.json KEY OBSERVED.json
```

每条先完成平台要求的授权和保存前复核。工具的命令只能记录/核验本地状态，不是跳过确认的入口。字段不一致或有多个可能的新增记录时保持 `uncertain`，交付待处理原因。

## 交付

实际新增条目和金额、已存在而跳过的条目、待确认项、实际账目ID及逐笔核验日志。范围内有任何未完成项，不能报告“全部补齐”，更不能自动清理所在邮件。
