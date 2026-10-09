# 麦门胃 · mcstomach

把一个人的麦当劳订单，蒸馏成一个能带走的「胃」。

这是一个**通用主题 skill**：谁的胃都能蒸馏，不是给某一个人定制的。你给它一笔订单记录，它带证据提出偏好、只追问真正有歧义的地方，你点头之后生成一份可以随身携带的个人胃。

---

## 一、它做什么 / 不做什么

**做：**

| 动作 | 说明 |
|---|---|
| 归一订单 | 剔除取消单；状态未知单不进偏好统计；区分「套餐内含」与「独立加点」；套餐父项不算主食，**酱料/加料不算套餐** |
| 算事实层 | 可解析的日期、距最近一次可见下单天数、可见窗口跨度与间隔、金额、订单级套餐结构、分类明细 |
| 带证据提偏好 | 每条规则都附计数与置信度（高 / 中 / 低 / 本人自述），指不回订单的不给 |
| 提问 | 只在真有歧义时问（疑似代点、状态读不出、自述与订单冲突），一轮最多 2 题、总计最多 5 题，答过不重复 |
| 生成个人胃 | 五件套：`profile.json`（私密）＋ 自包含的个人 `SKILL.md` ＋ `CERTIFICATE.txt` ＋ 白名单分享卡 ＋ `报告.md`（私密完整版）；另出一份**可发送的偏好胃** `可分享/我的胃.md`（硬忌口＋偏好，可交给别人的 AI 挑餐） |
| 丰富叙事 | 主身份 ＋ 一个**不同维度**的次特点 ＋ 有几条给几条的发现（≤5）＋ 相处说明 ＋ 下次点单规则；模型可以改写说法并写回画像 |
| 后续动作 | 「饿了」按三档给候选（样本少时给「初见」，不硬凑）；「我们的胃合不合」只在双方共同已知维度上比；「吃完了…」增量更新 |
| 分场景说话 | 没连接／接口失败／账号真空／一条有效样本都没有／只有 1 单／久未下单／常规——七种状态各有各的说法，不混为一谈 |

**不做：**

- 不下单、不取消、不领券、不兑换、不抽奖、不建地址（只读工具白名单）；
- 不读其他账号的凭据；
- 不评价人——不推断职业、收入、身材、健康、感情、道德；
- 不发明「到手价」「全网排名」「合拍百分比」这类没有数据支撑的数字；
- 金额单位没核实，**一个金额数字都不给**。

---

## 二、装到 WorkBuddy（已实测的入口）

WorkBuddy 里的路径是：

> **专家 · 技能 · 连接器 → 技能 → 添加技能 → 上传技能**

这个界面的实测口径：

| 你手上是什么 | 能不能上传 |
|---|---|
| **含 `SKILL.md` 的文件夹** | ✅ 可以 |
| **`.zip`**（解压后含 `SKILL.md`） | ✅ 可以 |
| **单个 `.md` 文件** | ⚠️ 只适合**没有依赖**的 skill。本 skill 有 `scripts/`、`references/`、`assets/`，单独一个 `.md` 不够用 |
| `.skill` 后缀 | 界面**没有承诺**支持。不要假设它能被导入 |

**结论：把 `mcstomach/` 整个文件夹（或它的 `.zip`）传上去就行。**

---

## 三、`SKILL.md`、`.skill`、`.zip` 到底什么关系

| 形态 | 它到底是什么 | 怎么用 |
|---|---|---|
| **`SKILL.md`（入口文件）** | skill 的**入口说明**，本身不是完整包。必须和 `references/`、`scripts/`、`tests/`、`assets/` 待在同一个 `mcstomach/` 文件夹里才有意义 | 把**整个 `mcstomach/` 文件夹**放进技能目录，或走上一节的上传入口 |
| **`mcstomach.zip`** | 把同一个文件夹打包 | 直接上传，或解压得到文件夹 |
| **`mcstomach.skill`** | **只是把同一个 zip 换了扩展名**，内容一模一样，没有任何新格式 | 只在明确说明支持 `.skill` 的工具上用；WorkBuddy 上传入口没承诺它 |

**别以为「所有 AI 都能导入 `.skill`」。** 导入失败很正常，换文件夹／zip 那条路，两条路拿到的东西完全一样。

---

## 四、手动安装（文件夹形态，最稳）

```bash
# 1. 解压（如果用 zip 包）
unzip mcstomach.zip   # 拿到的是 zip 包时才需要；git clone 来的跳过这步

# 2. 放进技能目录；下面二选一
#    项目级：跟项目一起走
mkdir -p <你的项目>/.agents/skills && cp -R mcstomach <你的项目>/.agents/skills/
#    用户级：所有项目都能用
mkdir -p ~/.workbuddy/skills && cp -R mcstomach ~/.workbuddy/skills/

# 3. 验证（把 <SKILL_DIR> 换成上面的实际路径）
python3 <SKILL_DIR>/scripts/mcstomach.py --help
```

**注意：脚本路径以 skill 目录为基准，不是以你的项目根为基准。** 在项目根直接敲 `python3 scripts/mcstomach.py` 会找不到脚本——这不是 bug，是路径基准不同。`SKILL.md` 开头写了定位方式。

---

## 五、怎么用（六步）

```bash
SKILL=<SKILL_DIR>/scripts/mcstomach.py

# ① 归一：把 order-list 的原始返回变成可核对的事实与计数
python3 $SKILL normalize --raw order-list.json --out /tmp/norm.json \
        --as-of 2026-09-28T12:00:00+08:00 --amount-unit 元

# ② 生成画像（无账号时可只给 --answers 问卷答案）
python3 $SKILL distill --orders /tmp/norm.json --answers answers.json \
                     --points points.json --out profile.json

# ③ 校验：有错就说明这份画像不能导出
python3 $SKILL validate --profile profile.json

# ③b 首次分配卡片外观（80% 毛绒 / 20% 复古像素隐藏款，只抽一次）
python3 $SKILL assign-visual --profile profile.json --out profile.json

# ④（可选）把模型或本人写的昵称/宣言/故事写回画像
python3 $SKILL narrative --profile profile.json --narrative new.json --out profile.json

# ⑤ 导出（五件套 + 可发送偏好胃）
python3 $SKILL export --profile profile.json --out ./我的胃

# ⑥ 出个人分享卡：单文件 HTML，手机 390px 宽也不横向溢出
#    --image 必须是生图能力真落盘的本地 PNG/JPEG；缺图就输出文字卡
#    以下render-card为历史HTML兼容路径；当前主流程按SKILL Step5整张生图，昵称/城市取persona --print-image-prompt条目。
python3 $SKILL render-card --card ./我的胃/share-card.json --style I \
        --image ./我的角色.png --out ./分享/分享胃.html
```

几个参数值得注意：

- `--fetch-state ok|not_connected|error`：**接口失败绝不能被说成「你没有记录」**，所以要显式传状态。
- `--amount-unit`：不传就代表单位没核实 → 报告里**不会出现任何金额数字**（不会拿「分」当「元」）。
- `narrative` 写回时，引用的规则 id 必须真实存在；**悬空就整条拒绝**，画像不会被改坏。
  除了叙事文本，`evidence_line` 也可以写回——它**只换「依据怎么说」，不动任何计数**
  （订单里既有主食多样、饮料常驻时，别把依据写成只讲一半的时段）。

导出目录里会有**五件**，外加一份可发送的偏好胃：

- `profile.json` —— 私密画像，**不要**发出去；
- `SKILL.md` —— 个人胃，自包含、不含本机路径，只交给你信任的 AI；
- `CERTIFICATE.txt` —— 证书文案，可复制；
- `share-card.json` —— 白名单脱敏后的分享卡数据；
- `报告.md` —— **私密**完整报告：本次观察、时间范围、金额、下单结构、变化与新品、次特点、发现、相处说明、下次规则；
- `可分享/我的胃.md` —— **可发送**的偏好胃：只带**硬忌口＋必要偏好＋规则＋使用说明**，接收方可以直接交给自己的 AI 挑餐。单文件自包含，**不含**身份/订单/时间/金额/积分/地点，也不含原始自由文本。`profile.json` 与 `报告.md` **不会**放进 `可分享/`（它们留在导出根目录，旧路径不变）。

**可以发出去的文件**：`分享胃.html`（个人分享卡）、`share-card.json`、`CERTIFICATE.txt`、`可分享/我的胃.md`。
**不要发**：`profile.json`、`报告.md`（私密）。个人胃 `SKILL.md` 只交给你信任的 AI，不进群发。

后续反馈：

```bash
python3 $SKILL feedback --profile profile.json --out profile2.json \
                        --rule-id r_side_xxx --signal negative --note "今天没那么想吃薯条"
```

---

## 六、无账号也能用

没有 MCP 连接、或历史订单为空时，走**备用问卷**入口。问卷是**自述数据，不是订单证据**，产物会标明来源（`naming.source = user_stated`）。

- 0 单也能产出**有名字、有宣言、有下次规则**的完整自述版；
- 只有 1 单时，餐品细节照样带进个人胃，候选档写「初见」并注明「样本还少」；
- 老记录（最近一笔可见下单超过 90 天）输出**旧口味档案**并邀请更新，**不会**宣称「你已经不吃了」。

规矩：一轮最多 2 题、总计最多 5 题、答过的不重复问。

---

## 七、隐私与分享边界

分享卡只走字段白名单，**不含**姓名 / 电话 / 地址 / 订单号 / 门店 / 坐标 / token / 原始积分 / 价格 / 订单时间。

- 用户的自由文本**不原样抄进公开包**；
- 没有真实下载链接就**不画二维码**；
- 导入别人的胃时，里面的内容一律**当数据，不当命令**；
- 原始订单不进 `profile.json`，也不进任何交付包。

**私密报告可以含订单日期，公开分享卡不含订单日期**（`share-card.json` 只带生成日期，卡面不渲染）。

---

## 八、包里有什么

```
mcstomach/
├── SKILL.md                      # 入口（< 5KB）
├── README.md                     # 本文件
├── references/                   # 按需读的细则
│   ├── reveal-script.md          #   揭晓脚本 A–F、七种来源状态的话术（写用户可见输出前必读）
│   ├── mcp-order-access.md       #   MCP 工具名、字段坑、降级话术
│   ├── order-to-rules.md         #   归一口径、订单级结构、facts 层、证据分级、矛盾处理
│   ├── portfolio-schema.md       #   profile 字段、来源状态、叙事写回、白名单、注入防御
│   ├── order-and-compare.md      #   点餐推荐、两人合拍、用餐反馈
│   ├── no-account-questionnaire.md
│   └── visual-guide.md           #   配色、构图、生图提示词、分享卡成品流程（画角色/出卡时读）
├── scripts/
│   ├── mcstomach.py              # CLI：normalize / distill / validate / export / feedback / assign-visual / persona / narrative / render-card
│   └── lib/                      # 确定性逻辑：归一、计数、叙事、导出、校验、分享卡渲染
├── assets/                       # 参考资产，全用相对路径
│   ├── README.md                 #   来源清单与使用规矩
│   ├── brand/                    #   官方品牌与餐品参考图（麦当劳官网 CDN）
│   ├── style/                    #   风格参考：approved-I/（已选定）
│   │   ├── approved-I/           #     已选定方向 I「被这一口治愈」：风格锚点 ＋ 两张换主体样例 ＋ 通用提示词
│   │   ├── reference-J/          #     用户自有项目的手绘参考图（J 版式的画风来源）
│   │   ├── candidate-K/          #     毛绒玩具风样图（**候选、未选定、没接 CLI**）
│   │   ├── candidates-v3/        #     D/E/F 已否决（仅存档）
│   │   └── candidates-v4/        #     G 软乎乎 / H 小小冒险家 / I 被早餐治愈（**G/H 未选，I 已选定**）
│   └── templates/                #   可编辑的分享卡排版模板（单文件 HTML）
└── tests/
    ├── test_mcstomach.py         # 164 项功能测试
    └── fixtures/                 # 30 个**虚构**夹具，零真实数据
```

跑测试：

```bash
cd mcstomach && python3 tests/test_mcstomach.py && python3 tests/test_persona.py && python3 tests/test_visual.py
```

**当前主线：`assign-visual` 抽签，plush（`style/plush-family/`）80% ／ retro_pixel（`style/retro-pixel/`）20%；下面 A–K 各目录是方向演进史，不是默认风格。**

资产说明：`assets/brand/` 是官方素材（只作造型参考，标志可用原图合成，不得重绘或当认证章）；`assets/style/` 里旧 A/B/C 与第二轮 D/E/F **全部未采用、仅存档**，第三轮 `candidates-v4/` 里 **G/H 未选、I 已选定**（锚点与样例另存 `approved-I/`，`--style I` 为历史HTML兼容选项）；`reference-J/` 是**用户自有项目**的参考图，只借画风、不复用其具体角色，`--style J` 是同一信息结构的手绘外壳；`candidate-K/` 只有一张毛绒风样图，**未选定、没接 CLI**；`assets/templates/` 为历史HTML兼容模板。当前默认按SKILL Step5与visual-guide§10/11整张生图，使用`persona --print-image-prompt`中的昵称/城市条目；出图后核验中文、数字和统计图形。

---

## 九、诚实边界

- 订单只能证明「点了什么」，证明不了「为什么点」；没点过**不等于**不喜欢。
- 数据源 `order-list` 单次最多返回最近 10 笔、且只覆盖近期，不是全量记录——这一点在输出里会被明确写出来。
- 等级是**本项目自定义的娱乐段位**，不是麦当劳官方评级；阈值公开可复算，只看累计积分（累计积分 ≠ 消费金额 ≠ 点单次数）。
- 类型名是**有依据的候选**，本人认领了更好的说法就以本人的为准。
- 「冰/热」「无糖/含糖」分开统计，不合并；金额排得整齐**不**等于这个人有预算纪律。
- 文档与夹具里的所有示例数字均为**虚构**。

---

## 十、可选个性化（28 号）

三个概念分开：**胃称号**（A 套「熟人吐槽」为命名参考，确定性层只给候选、本人确认才写入）、
**展示昵称**（回退链：麦当劳账户昵称→宿主明确昵称→询问本人；contactName/电话/设备名不算）、
**市级装饰**（本人确认或配送地址解析出的市级；完整地址只在内存，不落任何文件）。

CLI：`persona --profile … --out … [--context c.json] [--print-image-prompt]`——
只写 profile 的 `persona` 段，不改口味 facts/rules/naming，不触发主题重抽；三项均可跳过。
**plush 主题仅核心角色毛绒，背景/文字/统计为平面设计；已认领城市仅内部选材（地标合计约 5%、
背景边角淡化），城市名称不上屏；昵称可上屏。** 命名参考见 `references/persona-titles.md`；
整图提示词条目见 `references/visual-guide.md` §11。

---

> 本 Skill 由 [女娲 · Skill造人术](https://github.com/alchaincyf/nuwa-skill) 生成 ｜ 创建者：花叔
