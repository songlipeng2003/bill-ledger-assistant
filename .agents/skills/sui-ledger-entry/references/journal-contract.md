# 本地补录日志合同

脚本只保存本地计划与状态，不连接随手记，也不授权写入。仍要在真实页面查重、取得授权、操作并读取实际结果。日志不能证明页面操作本身已发生。

计划：

```json
{
  "schema_version": 1,
  "entries": [
    {
      "key": "BILL-REF-EVENT-A", "event_id": "event-a", "type": "income",
      "date": "2030-04-10", "time_precision": "date", "currency": "CNY", "amount": "10.00",
      "account": "card-a", "category": "退款",
      "memo": "BILL-REF-EVENT-A；Synthetic Shop退款；来源只有日期",
      "source_refs": ["synthetic://thread-a#row-1"]
    }
  ]
}
```

`expense`/`income`需要`account`及`category`；`transfer`需要明确且不同的`from_account`、`to_account`。金额为正的两位小数字符串。已知时分必须提供`time`（分钟精度HH:MM、秒精度HH:MM:SS），并在实际记录中重新核对该时间。日期精度可以不提供时间；界面强制时间时使用`time: "00:00"`及`time_is_placeholder: true`，保留占位性质。稳定`key`进入备注，并在重跑时沿用；银行多来源同一事件只计划一笔。

验证输入 `OBSERVED.json`：

```json
{
  "search_complete": true,
  "captured_at": "2030-04-10T08:00:00+00:00",
  "records": [
    {
      "id": "actual-ledger-a", "key": "BILL-REF-EVENT-A", "type": "income",
      "date": "2030-04-10", "time_precision": "date", "currency": "CNY", "amount": "10.00",
      "account": "card-a", "category": "退款",
      "memo": "BILL-REF-EVENT-A；Synthetic Shop退款；来源只有日期"
    }
  ]
}
```

真实验证快照使用实际读取的、带时区的当前捕获时间，30分钟内有效。不能仅在旧观察上改时间。`search_complete`只有读全相关检索后才为true。

状态为：`planned` → 保存前预占`submitted` → 实际重读匹配后`verified`；任何不确定、零条、多条、字段不符保持`uncertain`。同一实际账目ID不能验证两个不同事件。

脚本故意没有“重置到planned”命令。查证未保存后若确需重试，应明确说明当前证据并按受控的新计划处理，保留原不确定日志；不得绕过日志反复新增。终端本地记录不是写入许可。

已有日志不能被`init`覆盖。命令使用本地锁与原子写入，防止同时写日志或中断导致损坏；发现遗留锁先核查是否有正在执行的进程，不自动删除锁重试。
