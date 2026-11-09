# 根据原备注选择已有商家

这是随手记“商家”字段（`store`）的选择规则，不是支出分类，也不改写银行原始商户字段（`merchant`）或备注。读取项目 `config/bill-policy.json` 的 `merchant_selection`；仅适用于已获授权的后续录入，不回改历史账目。

## 实际录入

1. 在目标账本读取完整、当前的商家目录，保存真实商家ID、显示名称、账本名和捕获时间。目录截断、账号/账本不符或超过30分钟时先刷新，不拿历史目录冒充实时数据。真实目录和已核实别名映射留在 `runs/` 或 `config/accounts.local.json`，不提交。
2. 优先从原备注识别商家，必要时参考银行结构化商户字段。去除支付宝、AlipayHK等明确支付渠道前缀仅用于识别，不修改平台备注。可识别明确的“商家：…”字段、退款/充值文字及分隔的数字门店尾号；已有精确分店名称优先。不要按金额、账户、用途分类或公司名的大致相似度猜商家。
3. 在已有目录中匹配完整名称或已核实的同实体别名。中英文/常见简称的对应须有可靠依据；不自动生成模糊别名。重名、别名冲突、备注与结构化商户冲突、未建档都不自动选择，记录具体原因。
4. **每笔显式设置商家**：唯一明确匹配时选择该目录项；其余明确重置为“无”，不能沿用上一笔残留商家。不要点击商家旁的“+”创建新项。无匹配不阻止已授权账目按“无”录入，但须在交付中说明；用户另行指定商家时以其要求为准。
5. 保存前检查商家显示名和实际选择ID。计划记录 `store`、`store_id`；保存后打开实际账目详情重读相同字段，不只看仍留在表单里的选项。商家未保存、保存成上一笔或ID不符，保持 `uncertain`，查证实际记录后处理，不能重新点保存/再新增来碰运气，也不触发清理。

项目、成员仍遵循原规则。退款不变成支出，转账不因为备注有店名就变成消费。商家目录ID不是实际账目ID，也不替代来源、金额和一对一对账证据；账本快照不要把所选商家名擅自填成来源原商户/原备注。

## 可选的纯本地匹配助手

```sh
python .agents/skills/sui-ledger-entry/scripts/select_store.py INPUT.json \
  --policy config/bill-policy.json --out RESULT.json
```

脚本不联网、不控制网页、不创建商家、不记账，也不授予写入权限。其 `catalog.complete` 和时间须来自实际读取；脚本不能证明调用者确实读全了页面。复杂别名无法确认时保留“无”，不要为得到 `matched` 编造别名。

输入示例（仅合成数据；捕获时间须替换为实际读取时间）：

```json
{
  "schema_version": 1,
  "book": "Synthetic Book",
  "type": "expense",
  "memo": "支付宝-合成百货",
  "merchant": null,
  "catalog": {
    "book": "Synthetic Book",
    "complete": true,
    "captured_at": "2030-04-10T08:00:00+00:00",
    "stores": [
      {"id": "synthetic-store-a", "name": "合成百货", "aliases": ["Synthetic Shop"]}
    ]
  }
}
```

成功输出包含：

```json
{
  "schema_version": 1,
  "status": "matched",
  "store": "合成百货",
  "store_id": "synthetic-store-a",
  "memo": "支付宝-合成百货",
  "candidates": [{"id": "synthetic-store-a", "name": "合成百货", "evidence": [{"source": "memo", "method": "exact_name_or_verified_alias"}]}],
  "reason": "unique_existing_store_identity"
}
```

只有 `matched` 是已有商家选择建议。`unmatched`、`ambiguous`、`conflicting_sources`、`catalog_unavailable`、`not_applicable`、`disabled` 均返回 `store: "无"`、`store_id: null`，须保留原因。目录跨账本、过期/不完整，不能被当作“没有这家商店”的证据；必要时重新读取。
