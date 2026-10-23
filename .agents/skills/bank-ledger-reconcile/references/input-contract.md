# 本地对账脚本输入合同（schema_version 1）

脚本是保守的快照比较器，不解析网页、不提取邮件、不访问后台、不创建账目或删除邮件。需要人工根据真实读取证据标准化输入。自动通过仅表示所提供字段充分且一致，不证明底层快照真实完整。

## 输入包装

```json
{
  "schema_version": 1,
  "scope": {"start": "2030-04-01", "end": "2030-04-30", "accounts": ["card-a"]},
  "policy": {"date_windows": {"hsbc": 2}},
  "sources": [
    {
      "id": "source-a", "mailbox": "synthetic-mailbox", "locator": "synthetic://thread-a",
      "fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "captured_at": "2030-04-30T08:00:00+00:00",
      "message_ids": ["message-a"], "event_ids": ["event-a"],
      "complete": true, "inventory_complete": true, "review_blockers": []
    }
  ],
  "transactions": [
    {
      "id": "event-a", "bank": "hsbc", "type": "purchase", "date": "2030-04-10",
      "currency": "HKD", "amount": "10.00", "account": "card-a", "merchant": "Synthetic Shop",
      "observations": [{"source_id": "source-a", "message_id": "message-a", "item_ref": "row-1"}]
    }
  ],
  "ledger_snapshot": {
    "complete": true, "captured_at": "2030-04-30T08:00:00+00:00",
    "scope": {"start": "2030-03-30", "end": "2030-05-02", "accounts": ["card-a"]},
    "entries": [
      {"id": "ledger-a", "type": "expense", "date": "2030-04-11", "currency": "HKD",
       "amount": "10.00", "account": "card-a", "merchant": "Synthetic Shop"}
    ]
  }
}
```

此例只演示合同，不是本人的交易数据。真实 `fingerprint` 需要计算真实、完整快照的SHA-256，不能复制示例。快照需要时区时间戳；清理阶段会检查是否新鲜，不能把旧数据重新标注新时间。

## 字段和归一化

- `bank`使用小写稳定键（如`hsbc`、`cmb`、`cgb`），与政策配置键一致，不把展示名称混作政策键。
- 所有 `id` 唯一；同一账目重复翻页只保留一个实际ID，同一ID内容冲突时重新读取，不静默覆盖。
- `amount` 是正的、无千分位、两位小数字符串。退款方向用 `type` 表示。脚本拒绝浮点数、负数、零和额外小数位，不做静默舍入。不是两位小数的货币需要单独适配。
- `currency` 使用明确币种代码。未知 `amount`/`currency` 可为 `null`，输出信息不足，不作零元交易。
- 银行 `type`：`purchase`, `direct_debit`, `fee`, `interest`匹配账本`expense`；`refund`, `credit`匹配`income`；`repayment`匹配账本`transfer`的`to_account`；`transfer`匹配已知两个账户；`authorization`始终待确认。
- 银行和账本账户必须是可靠映射后的稳定键，不直接凭名称相似或金额猜映射。未知银行账户可为null，结果为信息不足。
- 银行还款 `account` 为信用卡目标账户。账本转账使用 `from_account`、`to_account`，不能用备注里的账户名代替结构化方向。
- `date` 为交易日期。利息/费用未列独立交易日时可以用 `date: null` 和 `period: {"start": "...", "end": "..."}`，只在该完整账单周期内找候选；不虚构具体日期。其他无日期事件保留信息不足。
- `posted_date`可保留，但脚本不会擅自用它替代交易日，日期窗按显式政策执行。
- `merchant`为从可见数据读出的商户；可用`merchant_key`表示已核实的同一商户别名，不能用大致相似的字符串生成同一个键。
- 商户缺失但其他字段可对应，结果为`matched_fields_only`，不自动进入清理。还款/明确双边转账凭目标/两边账户、日期、金额和方向核对，不要求渠道名等于资金来源账户名。
- 已核实利息/费用分类可在账目加`category_type: "interest"`或`"fee"`。无法确定时不填写该字段来强行匹配。
- 事件或实际账目可以列`review_blockers`，如类别明显待确认。即使金额等字段对应，仍为`needs_review`，不放行清理。

## 完整性与范围

`scope`是此次请求的日期与账户。输入中的事件不能超出请求范围；账本快照也必须符合自己的声明范围。汇丰±2天需要账本实际覆盖日期窗口边界及相关账户；没读足窗口就是`incomplete_ledger_coverage`。

每个事件的`observations`必须引用来源中真实的`message_id`和不同的条目位置；每个来源的`event_ids`必须等于它实际关联的全部事件。若月账单包含本次范围外事件，不要删去这些事件来放行清理：可标记该来源不完整并只作人工局部对账，或扩大对账范围后重新提取。脚本会拒绝引用缺失事件的来源。

双语/转发副本经过证据核实后作为同一事件的多个观察。同日期同商户同额的真实两笔则是两个事件；脚本不会按金额自动去重。

## 结果

`matches`逐事件列状态、候选和实际账目ID。多个可行的实质分配保持`ambiguous`；同额笔数不足保持`cardinality_conflict`。完全相同的两笔事件与两条等价账目数量相等时，允许按数量一对一分配，不混成一笔。

只有严格`unmatched`状态的金额进入分账户/币种未匹配汇总。账户/日期/金额候选、字段不足及授权通知单列复核数量，不计算为确定遗漏、损失或余额差。

`source_results`只有来源全覆盖、无阻断、所有事件严格`matched`时才标可准备清单。输出`report_id`为输入快照摘要，不能当作签名或自动授权。后续清理还要重新读取来源、确认范围并按环境要求执行前确认。
