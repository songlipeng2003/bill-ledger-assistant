# 本地补录日志合同

脚本只保存本地计划与状态，不连接随手记，也不授权写入。仍要在真实页面查重、取得授权、操作并读取实际结果。日志不能证明页面操作本身已发生。

## 备注与本地元数据分离

- `memo`只放账单/交易记录原有备注或摘要，不添加稳定key、补录说明、入账日、来源行号或时间占位说明。来源没有备注时允许`""`。
- 稳定`key`、`event_id`、`source_refs`、日期精度与实际账目ID只保存在本地，重跑沿用同一事件标识。
- 新的无标识备注计划必须提供`before_snapshot`：相关账户、日期和方向范围已完整读取的实际账目ID清单及真实捕获时间。无记录时`ledger_ids`可为`[]`，不能把未读完误记为空。
- 保存后在同一相关范围完整重读，不依赖平台备注包含key：只有一个新增实际ID且日期、账户、方向、币种、金额、分类、备注和已知时间均相符才核验通过。多个新增候选保持`uncertain`；不能用本地给候选加key的方式消除歧义。

计划示例（仅示意结构，`captured_at`须替换为本轮真实读取的时间）：

```json
{
  "schema_version": 1,
  "entries": [
    {
      "key": "BILL-REF-EVENT-A", "event_id": "event-a", "type": "income",
      "date": "2030-04-10", "time_precision": "date", "currency": "CNY", "amount": "10.00",
      "account": "card-a", "category": "退款",
      "memo": "Synthetic Shop退款",
      "source_refs": ["synthetic://thread-a#row-1"],
      "before_snapshot": {
        "complete": true,
        "captured_at": "2030-04-10T08:00:00+00:00",
        "ledger_ids": ["existing-ledger-a"]
      }
    }
  ]
}
```

`expense`/`income`需要`account`及`category`；`transfer`需要明确且不同的`from_account`、`to_account`。金额为正的两位小数字符串。已知时分必须提供`time`（分钟精度HH:MM、秒精度HH:MM:SS），并在实际记录中重新核对该时间。日期精度可不提供时间；界面强制时间时使用`time: "00:00"`及`time_is_placeholder: true`，只在本地日志/交付说明中标注占位，不追加平台备注。银行多来源同一事件只计划一笔。

验证输入`OBSERVED.json`示例（实际读取全部相关记录，不只挑选有利的那条）：

```json
{
  "search_complete": true,
  "captured_at": "2030-04-10T08:01:00+00:00",
  "records": [
    {
      "id": "new-actual-ledger-a", "type": "income",
      "date": "2030-04-10", "time_precision": "date", "currency": "CNY", "amount": "10.00",
      "account": "card-a", "category": "退款",
      "memo": "Synthetic Shop退款"
    }
  ]
}
```

保存前快照和保存后观察均使用带时区的真实捕获时间，30分钟内有效；后观察不能早于前快照。不能仅改写旧观察时间。`search_complete`只有读全相关检索后才为true。脚本为旧日志保留备注含key时的验证兼容分支，但不要求或建议新账目这样写备注；不要改写历史日志来迁就新规则。

状态为：`planned` → 保存前预占`submitted` → 实际重读匹配后`verified`；任何不确定、零条、多条、字段不符保持`uncertain`。同一实际账目ID不能验证两个不同事件。

脚本故意没有“重置到planned”命令。查证未保存后若确需重试，应说明当前证据并按受控的新计划处理，保留原不确定日志；不得绕过日志反复新增。终端本地记录不是写入许可。

已有日志不能被`init`覆盖。命令使用本地锁与原子写入，防止同时写日志或中断导致损坏；发现遗留锁先核查是否有正在执行的进程，不自动删除锁重试。
