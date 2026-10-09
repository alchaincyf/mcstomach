# 麦当劳 MCP 接入与字段解读｜版本 v1.0.9（2026-09-28 官网复核）

> 快照来源：`调研/官网接入指南-open.mcd.cn-20260928.md`（`https://open.mcd.cn/mcp/doc`）。
> ⚠️ **快照过期风险**：以下工具名与字段来自 2026-09-28 的文档快照。**实际调用前必须先跑 `tools/list`，以真实返回的 name / inputSchema 为准**；如果和这里不一致，**听它的**。文档可能更新，也可能 MCP Server 版本与文档不同步。

---

## 1. 连接信息（来自快照）

| 项 | 值 |
|---|---|
| 接入地址 | `https://mcp.mcd.cn` |
| 传输 | Streamable HTTP |
| 鉴权 | 请求头 `Authorization: Bearer <MCP_TOKEN>` |
| 限流 | 每个 Token **600 次/分钟**，超限返回 `429` |

配置形态（**不要在本文件里写死任何真实 token**）：

```json
{ "mcpServers": { "mcd-mcp": { "type": "streamablehttp",
  "url": "https://mcp.mcd.cn",
  "headers": { "Authorization": "Bearer YOUR_MCP_TOKEN" } } } }
```

Token 由用户自己在 `open.mcd.cn` 控制台申请。**本 skill 不读、不存、不转发 token**，也不扫描任何第三方凭据文件。

---

## 2. 允许调用的工具（白名单，两个）

| Tool Name | Description（官方） | 用途 |
|---|---|---|
| `order-list` | 查询历史订单 | 蒸馏口味的唯一数据源 |
| `query-my-account` | 我的积分查询 | 仅用于娱乐段位 |

两者都**无需入参**。

### 绝对不许碰（写操作 / 资产操作）

`create-order` `cancel-order` `delivery-create-address` `auto-bind-coupons` `draw-lottery` `mall-create-order` `party-order-create` `mall-points-products`(兑换向) 等——只要具有下单、取消、领券、兑换、抽奖、建地址、改资产性质的，一律不调用。

**本 skill 停在推荐，不下单。** 用户真要下单，另起流程、明确门店/取餐方式/明细/最终试算价并取得确认，那时才谈得上下单工具。

---

## 3. `order-list` 返回结构（2026-09-28 快照）

路径：`data.list[]`，最多 **10 笔**「近期」订单，**不含麦麦商城订单**（商城单是 `mall-order-list`）。

```jsonc
{
  "orderId": "123456789010010",
  "orderType": "1",
  "createTime": "2026-07-07 11:03:35",
  "beType": "1",
  "beCode": "",
  "storeCode": "xxxxxx",
  "storeName": "xxxx",
  "orderStatus": "订单已完成",        // 快照中出现过："订单已完成" / "订单已取消"
  "orderProductList": [
    {
      "productCode": "9900006248",
      "productName": "苹板支撑便民套餐",
      "quantity": 1,
      "comboItemList": [                 // 套餐内含
        { "productCode": "1406", "name": "新升级板烧鸡腿堡_1547", "quantity": 1 },
        { "productCode": "6102", "name": "苹果片一袋(60g)", "quantity": 1 },
        { "productCode": "3071", "name": "无糖可口可乐中杯", "quantity": 1 }
      ]
    }
  ],
  "realTotalAmount": "40"
}
```

### 字段坑位

| 字段 | 规则 |
|---|---|
| `orderStatus` | 只认得快照里出现过的字面值。**"订单已取消" → 排除**；**"订单已完成" → 有效**；**其他未知字面值 → 保留但标 `status_unconfirmed`，证据权重降级**，并在 D 核对点提示。绝不把未知状态默认成「已完成」。 |
| `comboItemList` | **内含 ≠ 主动加点。** 同一个「无糖可乐」出现在 comboItemList 里，和作为 `orderProductList` 顶层项独立出现，是两种不同的证据强度（后者更强）。详见 `order-to-rules.md`。 |
| `createTime` | 用于时段归类（早餐/午晚/夜间）。**只做粗时段，不推断作息或职业。** 时间本身不进出口包。 |
| `realTotalAmount` | 单位未明示，快照示例为整数字符串。**不能据此算单价、不能算「到手价」、不推断消费水平**；只可在服务端算「区间稳定/波动」这一个无量纲结论。不进出口包。 |
| `storeCode` / `storeName` | **不进出口包**（等于泄露常去地点）。 |
| `beType` / `beCode` | 含义未在文档中说明。**留空，不猜。** |
| 字段缺失 | **留空，不推断、不补默认值。** |

### 以下三点必须记住

1. **非全量**：最多 10 笔，且是「近期」。措辞永远写「读到最近 N 笔」，**绝不**写「你一共点过 N 次」。
2. **重复项不合并**：快照示例里同一套餐出现了两次作为两个独立 entry——按实际结构计数，**不要自作主张去重**，也不要凭空翻倍。
3. **团体单不直接算个人偏好**：见 `order-to-rules.md` 的识别规则。

---

## 4. `query-my-account` 返回结构（快照）

```jsonc
"data": {
  "availablePoint": "7592",
  "accumulativePoint": "141760.94",   // 累计积分
  "currency": "麦当劳积分",
  "currentMouthExpirePoint": "0",
  "expiredPoint": "0",
  "frozenPoint": "30",
  "lastMouthExpirePoint": "0",
  "nextMouthExpirePoint": "0",
  "usedPoint": "115474.14"
}
```

### 硬规则

- `accumulativePoint` 是**累计获得积分**。⚠️ **它不是消费金额，不是点单次数，不是客单价**。拿它除以任何东西得出「平均每次花多少」都是错的。
- 隐私红线：以上积分字段**一个都不进出口包**（白名单见 `portfolio-schema.md`）。只有「娱乐段位称号」可选择性公开。
- **调用失败 / 无账号 / 字段缺失 → `point_state = "unknown"`，显示「待认证」**，不许默认最低档、不许编一个数。

---

## 5. 降级路径：没有 MCP 时怎么说话

本会话**未配置**麦当劳 MCP 工具时的标准话术：

> 「这个会话里我没接上麦当劳 MCP——看不到你的真实订单。
> 我可以走备用入口：问你最多 5 个问题，也能做出一份胃，但**那里面的依据是**你自己说的，不是订单算出来的。**两者在卡片上会标明来源不一样。**
> 想接真实订单的话：去 open.mcd.cn 申请 MCP Token 配到客户端，回来再说一声。」

**要求**：
- 必须先如实说明「没接上」，沉默切问卷 = 欺骗。
- 产出的 profile 里 `source.kind` 必须标 `questionnaire`，卡片上标注来源差异。
- **绝不**因为没有 MCP 就把示例数据、虚构订单当成真实结果输出。
- **绝不**因为「生成了一段流畅的文字」就宣称 MCP 已连通。连通的唯一证据是 `tools/list` 或实际工具调用的真实返回。

---

## 6. 一句话入门（给不懂命令行的用户）

> 对着任意装了这个 skill 的 AI 助手说一句：**「蒸馏我的胃」**。就这一句，剩下的它问你。
