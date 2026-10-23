# 可追溯的交易输出

每笔事件保留正的绝对金额和显式类型，不依赖正负号推断方向。金额是无千分位、两位小数的字符串，例如 `"12.30"`，不是浮点数；本工具按“分”核对，非两位小数货币需要另行适配，不能静默四舍五入。

事件最少字段：`id`, `bank`, `type`, `date`, `currency`, `amount`, `account`, `merchant`, `observations`。账户是已确认映射的稳定键，不是凭同额猜的账户名。未确认的字段可以先保留为 `null`，但尚不能投入严格脚本或标记可清理。

- `type`：`purchase`, `direct_debit`, `refund`, `credit`, `fee`, `interest`, `repayment`, `transfer`, `authorization`。
- `date`：交易日期，ISO格式。仅入账日已知时不要冒充交易日；记录差异并让对账端处理。
- `posted_date`、`time_precision`、`raw_merchant`、原币信息可作为附加字段。
- `transfer` 需要 `from_account`、`to_account`；一个方向未知不能自动判定完整匹配。
- `observations` 每条含 `source_id`, `message_id`, `item_ref`；例如某消息中第3个真实交易条目。真实同额两笔的 `item_ref` 不相同。

来源最少字段：`id`, `locator`, `fingerprint`, `message_ids`, `event_ids`, `complete`, `inventory_complete`, `review_blockers`。`event_ids` 必须包含该会话的全部交易，不只是本次想清理的交易。

`fingerprint` 是规范化快照的 SHA-256，用于检测变化，不是交易身份或可信度证明。应对所有消息的正文、附件内容摘要/未读取状态、消息ID和明细顺序一起计算。仅可去除已识别的相对时间显示、按钮提示等动态UI；不可丢掉新回复或附件。脚本只检查格式与一致性，真实性仍依赖实际读取证据。

不完整来源、无金额通知及未能关联的消息保留在覆盖说明；不能从来源清单中删除来让清理条件通过。Gmail会话包含未处理消息时，`inventory_complete: false` 或添加阻断原因。

若要使用配套本地对账脚本，读取 `bank-ledger-reconcile` 的输入合同；它是完整输入包装的约定，不要求提取技能单独安装时存在该脚本。
