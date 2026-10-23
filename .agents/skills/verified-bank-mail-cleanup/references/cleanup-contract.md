# 清理清单准备合同

`prepare_cleanup.py`只做本地交集校验，不操作任何邮件。输入是对账报告与实际重新读取的来源快照。生成的清单始终为`execution_authorized: false`并要求执行前确认，任何JSON字段都不替代真实用户授权。

## REPORT.json

配套对账脚本会生成以下字段；单独使用本技能时也可根据真实核对证据人工准备同样结构：

- `schema_version: 1`、`report_id`（SHA-256摘要，不是签名）；
- `ledger_captured_at`：带时区的实际账目读取时间；
- `sources`：`id`, `mailbox`, `locator`, `fingerprint`, `captured_at`, `message_ids`, `event_ids`, `complete`, `inventory_complete`, `review_blockers`；
- `matches`：每个`event_id`的`status`和`ledger_id`，只接受严格`matched`且不同事件不复用账目ID；
- `source_results`：每个`source_id`的`eligible`。脚本会额外检查事件和来源条件，不仅相信该布尔值。

## FRESH_SOURCES.json

```json
{
  "schema_version": 1,
  "mailbox": "synthetic-mailbox",
  "requested_source_ids": ["source-a"],
  "sources": [
    {
      "id": "source-a", "mailbox": "synthetic-mailbox", "locator": "synthetic://thread-a",
      "fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "captured_at": "2030-04-10T08:00:00+00:00", "location": "inbox",
      "message_ids": ["message-a"], "event_ids": ["event-a"],
      "complete": true, "inventory_complete": true, "review_blockers": []
    }
  ]
}
```

真实指纹按所有消息/附件内容与库存计算；这里只是合成示例。快照列表使用新捕获的当前时间。读取规则中不能丢弃回复、附件或退信来保持指纹不变。

`requested_source_ids`是此次明确要处理的固定范围。缺失的重读来源只进入跳过列表，不用历史缓存代替。邮箱/定位、消息组成、事件组成、指纹必须一致；当前来源和账本证据默认最多30分钟，时钟超前超过2分钟也不通过。可以由操作者显式缩放证据有效期（1～1440分钟），这只影响准备清单，不取消操作前重新查看和确认。

位置为`trash`时列原已在垃圾箱，不能记成本次移入。来源完整、附件已读、无待确认项、所有事件强匹配才会产生候选。营销或无交易通知不会因事件列表为空自动通过。

## 人工执行结果

按候选来源定位、重读后才操作。逐封保存 `source_id`、`manifest_id`、`status`（`moved_to_trash` / `already_in_trash` / `skipped` / `failed_or_uncertain`）、核验的实际标签/恢复入口及时间。不要把清单生成成功称作删除成功。
