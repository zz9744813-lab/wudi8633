# 玄鉴 XuanMirror —— 项目交接文档（给接手的 AI 智能体）

> 生成时间：2026-08-30。本文档目标：让你**在不看历史对话的前提下**，完整理解项目、能跑起来、能安全地做优化。所有命令均为实测可复现，坑点全部列出。

---

## 1. 一句话定位

**玄鉴是一个「个人智能未来预测、验证与自校准系统」**，不是算命聊天机器人。

核心闭环（这是整个系统的灵魂，所有代码都围绕它）：

```
Prediction → Freeze → Reality → Verify → Score → Diagnose → Learn → Predict Again
    预测      冻结     现实对照   验证      评分      归因      学习       再预测
```

真正的产品不是命盘，是**一个不断被现实检验的个人 Future Model**。术数（八字/六爻/紫微…）在这个系统里只是「待验证的信号」，和 Reality（现实事件）、Null Model（贝叶斯基线）并列，谁准谁在融合里拿更高权重——**如果 Null 比术数强，系统必须承认术数没有贡献**（宪法 C-006）。

---

## 2. 当前状态快照（已核实的结论）

| 项 | 值 |
|---|---|
| 完成度 | **方案 v1.0 十项验收标准（PRED-01…EXP-01）全部达成** |
| 测试 | `147 passed, 2 skipped`（全绿；round 10-18 累计新增 51 项；弃用警告已清零，仅余 1 条 FastAPI 自身提示） |
| 公众人物回测 | `tools/backtest_figures.py`（round 16 扩至 74 人 × 165 事件，负向 13）：年柱 74/74 ✅；**zhouyi 已退出方向投票**（词频基线灌水，经文仍作参读）；liuyao 打分重定心+中性带（41%→p0.19 硬币带）；qimen 人事主断 58%；ziwei 80% 偏正；融合弱先验带 [0.42,0.55]（ziwei 0.55/meihua 0.47/liuyao 0.44/zhouyi 0.42，用户实证 skill 覆盖）；采样时辰伪随机化（固定时辰曾使梅花 77% 负偏）；报告落 docs/回测报告-公众人物.md |
| 数据库 | SQLite，**38 张表**（+`system_fortune_readings` 紫微批示缓存） |
| 术式引擎 | 8 个全部真实可跑（八字/紫微/六爻/梅花/周易义理/奇门/掌纹/面相；round 12 新增 zhouyi 义理派：卦辞+爻辞吉凶断辞定向，易卦三法同组去相关） |
| Agent | 21 个业务 Agent + 3 个基类（Blind Multi-Agent 架构） |
| 对抗审查 | 14 种攻击 + 串联 Gate |
| 校准架构 | **三阶段**（2026-08-31 第二轮治本）：cold(<5) 基线校准 → explore(5~19) 信号弱先验实证 → formal(≥20) 正式预测 |
| 研究期多尺度 | `RESEARCH_SCALE_PLAN = 日3/周2/月1`（2026-09-02）：研究期每轮扫三个时间尺度，描述自动带日期（「9月3日（周四）…」/「9月3日~9月9日这一周…」/「2026年9月…」） |
| 命理批示 | 八字 `GET /api/fortune/reading`；紫微 `GET /api/fortune/reading/{system}`（ziwei，2026-09-02 新增）。reasoning 层失败自动回退 cheap 层（见坑 15） |
| 今日锦囊 | `GET /api/fortune/daily`（2026-09-03）：lunar-python 当日宜忌/值神/冲煞/三神方位/吉时/彭祖百忌 + 河图数幸运数字（五行取数：水1/6、火2/7、木3/8、金4/9、土5/0）+ 五行幸运色 + 个人层（日主×日支十神关系、红鸾/天喜/咸池动情、犯冲提示）。全部确定性计算，零 LLM |
| 多法交叉选题 | 研究期/正式期都先跑 6 术式信号，按「同向术式数」排序选题（2026-09-03）。≥2 法同向 → 卡片带「◆ N 法交叉印证」徽标 + ✓/✗ 术式徽标。**交叉只决定选题与详批，概率仍归 Null/融合（C-005 不动）** |
| 周易经文层 | `app/core/zhouyi`（2026-09-05，round 10）：通行本 64 卦卦辞+大象传+386 爻辞数字化抄本，查询 API（`cite/by_pattern/by_lines/gua_ci/yao_ci`），卦名↔阴阳串映射**复用六爻 HEXAGRAMS 单一事实源**。六爻/梅花 adapter 信号证据挂「经文参读」（静卦读卦辞、动爻加读爻辞）；锦囊增「日卦·周易经文」确定性卡（日粒度起卦）；详批叙事增「全法盘点」（五术+掌面 ✓/✗/分歧/○ 全量，含未表态者）与「经文献录」。**经文是文献参读非效力宣称（C-006），只进读侧叙事，绝不进冻结描述**——测试反向证明经文原文含「小人」等词，泄漏必被 DefinitionAttack 拦截（`tests/test_zhouyi_canon.py` 14 项） |
| 详批叙事层 | `app/services/cross_engine.py` + `app/prediction/narratives.py`（2026-09-03）：常见情景/多法印证明细（每条带真实证据串）/建议/注意/幸运参考。**概率口径行明示「信号概率 vs 日常基线」**（持平/抬高/压低三态）；**注意（示警）常显**，有示警术式必点名（「八字、奇门 示警」）。关键架构：**叙事是读侧确定性重建（list/detail 接口现场拼），冻结仓里只存「何时+何事」短声明**——否则长文案必被 Gate 的模糊词攻击拦截（见坑 16） |
| 研究期去重 | 冻结前按 (event_type, time_scale, 窗口日) 去重，与在库样本重复的选题跳过并在 notes 注明（2026-09-03，见坑 19） |
| 影像相法 | `POST /api/imaging/analyze`（2026-09-03，round 17 存档化）：面相/掌纹照片上传分析。隐私边界（第 64 节）**只管原图**：临时文件 `finally` 即焚、`local_image_path` 恒 None、任何二进制不入库；**派生特征经用户确认后入 `palm_features`/`face_features` 表**（schema 早有此表，round 17 才接通写入），供长期前后对照 + `GET /imaging/history` + `DELETE /imaging/records`；palm/face adapter 无现传照片时回退最近存档特征出信号（`from_store` 标注）→ 相法信号持续参与预测闭环。云端详批=双闸门不变。未检出时诚实返回重拍建议 |
| 大运交互 | 命盘页大运改为可点时间轴（2026-09-03）：每柱显示干/支十神标签，点击运柱展开十年基调详条（`DAYUN_NOTE` 十神映射，前端纯计算零请求），流年卡同步鎏金高亮所选运覆盖年份；顺带修了「最后一柱大运永远标不上当前」的旧 bug |
| 术式仪式动效 | `frontend/src/components/rituals.tsx`（2026-09-03）：生成中逐术式轮动（六爻铜钱摇卦/梅花数字卷帘/奇门九星转盘/八字四柱翻字/紫微布宫流光/掌纹描线/面相三停扫描），完成后七术式收录态；命盘页批示等待同款仪式。纯 CSS/内联 SVG，`prefers-reduced-motion` 有降级 |
| 出生档案 | 创建 + `PUT /api/users/{id}/profile` 更新（之前只有 create，2026-08-30 补） |
| 紫微运限 | `ziwei-0.2.0`：iztro `chart.horoscope()` 流日/流月/流年接入信号层——流年宫宿主本命宫 + 流年干四化（十干四化表）引动本命星，按权重计入方向/强度。时标映射：day→流日、week/month→流月、year→流年 |
| 前端 | React + TS + Vite，8 个一级页面（未来页=按应验日分组、验证页=批复式、命盘页=八字+紫微十二宫盘） |
| git 远端 | `zz9744813-lab/suan` main |
| 原 NovelForge | 完整镜像备份在 `F:\agi\_suan_backup\suan.git`（含 5 分支+5 PR） |

**重要**：这是「方案 v1.0 按验收标准全部实现并测试通过」的状态，但**不是长期跑过的生产系统**——预测样本量目前为 0，可靠度矩阵、校准曲线、Shadow 学习都还是「代码在、没数据喂」的状态。见第 12 节「待优化方向」。

---

## 3. 技术栈

- **后端**：Python 3.13.12（managed）· FastAPI · SQLModel · SQLite · Pydantic v2 · httpx · APScheduler
- **术数引擎**：lunar-python（八字/历法）、iztro-py（紫微，纯 Python）、六爻/梅花（自研）、奇门（移植自开源 CLI）
- **CV**：OpenCV（掌纹/面相，传统 CV，非深度学习模型）
- **前端**：React 18 · TypeScript · Vite 5 · Tailwind · ECharts · echarts-for-react
- **测试**：pytest

---

## 4. 环境事实（★★★ 最重要，接手必读 ★★★）

### 4.1 Python 解释器（必须用这个，不要用系统 Python）

```
C:\Users\6\.workbuddy\binaries\python\envs\default\Scripts\python.exe   # managed 3.13.12，所有依赖都装在这
C:\Users\6\.workbuddy\binaries\python\envs\default\Scripts\pip.exe
```

- 系统里还有一个 `F:\Hermes\hermes-agent\venv\Scripts\python.exe`（3.11.16），**不要用**——它没有玄鉴的依赖。
- 装新依赖也装进上面这个 venv，不要 `pip install` 到全局。

### 4.2 Node（前端构建）

之前构建命令是 `cd frontend && npm run build`（npm 在 PATH 里，直接用即可）。managed node 在 `C:\Users\6\.workbuddy\binaries\node\versions\22.22.2\node.exe`（如遇 npm 异常可指定它）。

### 4.3 ⚠️ 系统代理陷阱（曾导致全链路挂死）

这台机器设置了全局代理：**`HTTPS_PROXY=127.0.0.1:2080`**（也影响 http）。

- 任何走这个代理的外网请求都会**挂起**（不是报错，是卡死直到超时）。
- 玄鉴的 LLM Provider 已经用 `httpx.Client(trust_env=False)` 绕过了（见 `app/providers/base.py`）。
- **但你写新代码时注意**：如果用 `requests`、`curl`、`aiohttp` 或任何新的 HTTP 客户端，必须显式禁用代理。`curl` 加 `--noproxy "*"`，`httpx` 加 `trust_env=False`。
- 排查网络问题时先怀疑这个，别先怀疑代码。

### 4.4 LLM 中转站（107.172.138.14:3000，2026-09-03 起）

配置在 `.env`（已在 `.gitignore`，不入库，key 别提交）：

- **`107.172.138.14:3000` 当前在用**（2026-09-03 用户换回此站，此前一度失效）。repo `.env` 与 `dist/.env` 两份要同步改——exe 读的是 dist 那份，PyInstaller 不会覆盖它。
- 2026-09-03 本站台模型实测（UTF-8 体 + 充足 max_tokens，命理题）：

| 模型 | 延迟 | 类型 | 结论 |
|---|---|---|---|
| `glm-5.3-flash` | ~35s | 推理（reasoning+content 分离） | **reasoning 层** |
| `agnes-2.5-flash` | ~4s | 轻推理，答得准 | **cheap 层 + vision 层**（已实测能读图） |
| `moonshotai/kimi-k3` | ~37s | 正文好但慢 | 备用 |
| `deepseek-v4-flash` | 60s 超时 | — | **此站不可用，别选** |
| `glm-5.3` | 返回空（supported_endpoint_types 为空） | — | **别选** |

- 旧站 `qiyovo.com:3000` 已于 2026-09-03 下线弃用；其 2026-08-31 实测表与坑（SSE 兼容、`response_format` 挂起、软错误重试）对本站**依然可能适用**——这些防护都在 provider 层，与本站无关，别删。
- **推理模型必须给足 max_tokens**（glm 系思考会吃掉额度，不够则 content 为空）——这是历次踩坑的铁律。
- **Windows 下 curl 直接传中文 JSON 会变 GBK 乱码**，模型只会回「乱码，请重发」。手测中转站时：把 JSON 写成 UTF-8 文件再 `--data-binary @file` 并带 `charset=utf-8`。

### 4.5 术数引擎的历史排坑结论

- **sxtwl**（寿星历）：Windows 无预编译 wheel，`pip install sxtwl` 编译失败 → 所以六爻/奇门**没有**用依赖 sxtwl 的现成库，而是自研/移植，历法统一走 `lunar-python`。
- **mediapipe 1.0.1** 移除了旧 `solutions` API（改成 tasks API 且要下载模型文件）→ 掌纹/面相改用 **OpenCV 传统 CV**（`app/core/palm/cv.py`、`app/core/face/cv.py`），无需模型文件、开箱即用。
- **iztro-py**（紫微）的 `astro.by_solar(date_str, time_index, gender)` **第二参是「时辰索引 0-12」不是小时**：`index = min((hour+1)//2, 12)`。踩过这个坑。

---

## 5. 目录结构与模块职责

```
xuanmirror/
├─ app/
│  ├─ main.py             FastAPI 入口（lifespan 里接 Scheduler）
│  ├─ config.py           pydantic-settings 配置（读 .env），所有开关都在这
│  ├─ database.py         引擎 + 建表 + 会话注入（SQLite，V2 才迁 PG）
│  ├─ api/routes/         predictions / analytics / system 三组路由
│  ├─ core/               ★ 术式引擎层（每个术式一个包）
│  │  ├─ calendar/        Calendar Core（lunar-python 封装，八字四柱/十神/大运）
│  │  ├─ bazi/ ziwei/ liuyao/ meihua/ qimen/ palm/ face/
│  │  │                   └─ 每个包 = engine.py(确定性排盘) + adapter.py(盘面→Signal)
│  │  └─ base.py          Adapter 注册表 registry + AdapterQuery 输入模型
│  ├─ agents/             ★ 21 个业务 Agent（Blind 架构，见第 7 节）
│  ├─ adversarial/        14 种攻击 + Gate（见第 8 节）
│  ├─ calibration/        Brier / LogLoss / Calibration / Sharpness / Skill Score
│  ├─ learning/           归因 / 可靠度矩阵 / 规则提升
│  ├─ reality/            RealityState + Null Model
│  ├─ prediction/         Prediction Budget + Ontology
│  ├─ providers/          LLM Provider 抽象（reasoning/cheap/vision 三层）
│  ├─ models/             SQLModel 数据表（37 张）
│  ├─ schemas/            Signal / Prediction / Outcome 等传输模型
│  └─ services/           编排层：pipeline / learning / ablation / future_tree /
│                         counterfactual / exports / reports / fortune
├─ tests/                 conftest + smoke + 各模块 + golden + acceptance
├─ docs/                  CONSTITUTION.md(宪法) / ENGINES.md / 工程方案_v1.0.md
├─ rules/                 Rule Registry（YAML，目前只有 bazi.yaml 示例）
├─ prompts/               Prompt 版本库（constitution.txt）
├─ reports/               Obsidian/日报周报月报输出目录（当前空）
├─ frontend/              React 前端（8 页面）
└─ .env / .env.example    本地配置（.env 不入库）
```

---

## 6. 怎么跑起来（实测命令，逐条可复制）

### 6.1 跑测试（最常用，改完代码先跑这个）

```bash
cd /f/agi/xuanmirror
PYTHONPATH=. XUANMIRROR_DB_URL="sqlite:///./data/xuanmirror.db" \
  "C:/Users/6/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -m pytest tests/ -q
```

预期：`74 passed, 2 skipped`。2 个 skipped 是 live LLM 测试（`test_live_llm.py`），加 `XUANMIRROR_LIVE_LLM=1` 才会真调中转站。

**注意**：测试里 LLM 已被 mock（`tests/conftest.py` 有 mock fixture），所以测试飞快、确定性、不依赖网络。这是刻意设计——验收测试不该因为中转站抖动而红。

### 6.2 启动后端

```bash
cd /f/agi/xuanmirror
PYTHONPATH=. "C:/Users/6/.workbuddy/binaries/python/envs/default/Scripts/python.exe" \
  -m uvicorn app.main:app --port 8765
```

- 端口 **8765**（故意避开 Hermes 等本地服务）。API 文档在 `http://127.0.0.1:8765/docs`。
- 默认 `SCHEDULER_ENABLED=true`，调度器不会自动跑。

### 6.3 启动前端

```bash
cd /f/agi/xuanmirror/frontend
npm run dev        # http://localhost:5173，/api 代理到 127.0.0.1:8765
npm run build      # 生产构建，ECharts 单独分包
```

前端默认用户 `DEFAULT_USER_ID = 1`（`frontend/src/api/client.ts`）。

### 6.4 git

```bash
cd /f/agi/xuanmirror
git status && git log --oneline -5
# 远端：https://github.com/zz9744813-lab/suan.git  （main 分支）
```

**协作约定**（用户习惯）：提交前先 `git status` 看差异、只提交变更部分、直接合 main 不开分支；不要 `git push --force`（除非用户明确说清空）。

---

## 7. 21 个业务 Agent（Blind Multi-Agent）

设计要点：**每个 Agent 只拿到自己的输入，不存在任何 Agent 读别人结论的路径**（防共谋，对应 AgentCollusionAttack）。

| 层 | Agent | 职责 |
|---|---|---|
| 基类 | BaseAgent / DeterministicAgent / AdversarialAgent | 抽象基类 |
| 扫描 | FutureScannerAgent | 从现实状态扫候选预测事件 |
| 信号 | BaziAgent / ZiweiAgent / LiuyaoAgent / MeihuaAgent / QimenAgent / PalmAgent / FaceAgent / MetaphysicalAgent | 各术式 → Signal |
| 现实 | RealityAgent / NullAgent | 现实事件信号 + 贝叶斯基线 |
| 生成 | CandidateAgent | 候选预测 → 概率 |
| 审查 | SkepticAgent / AdversarialAgent | 质疑 + 对抗审查 |
| 冻结 | FreezeAgent | 冻结预测（哈希防篡改） |
| 验证 | OutcomeCollectorAgent / OutcomeJudgeAgent | 收集结果 + 判定命中 |
| 学习 | AttributionAgent / LearningAgent | 归因 + 学习 |
| 报告 | ReportAgent / FirstPrinciplesAuditAgent / CalibrationAgent | 周月报 / 第一性审计 / 校准 |

LLM 是「增强」不是「核心」——管线核心逻辑（预算、Gate、冻结、评分）全是确定性代码，LLM 挂了系统照常降级（ABSTAIN）。

---

## 8. 14 种对抗攻击 + Gate

`app/adversarial/attacks/deterministic.py`，全部确定性实现：

Vagueness（模糊）· Barnum（巴纳姆）· Definition（定义漂移）· TimeWindow（时间窗）· CherryPick（摘樱桃）· MultipleTesting（多重检验）· Retrofitting（事后改口）· OutcomeLeak（结果泄漏）· SelfFulfilling（自我实现）· Baseline（基线）· AgentCollusion（Agent 共谋）· CorrelatedEvidence（相关证据）· ConfirmationBias（确认偏误）· NarrativeExcuse（叙事借口）。

Gate 是**串联**的：任一攻击命中即拦截，预测不得进入正式账本。

---

## 9. 核心数据流（预测闭环怎么走）

`app/services/pipeline.py` 的 `DailyPipeline.run()` 是主编排：

```
RealityState 扫描 → 候选事件(candidates)
  → 每候选：收集 Signal（Null + Reality + 各术式 Adapter，Blind）
  → Fusion 融合（Null Model 贝叶斯收缩，权重来自可靠度矩阵）
  → 对抗 Gate 审查（14 攻击，任一命中拦截）
  → Freeze 冻结（sha256 防篡改，冻结后不可改，只能 v1→v2）
→ 次日到期 → VERIFY_REQUIRED 进验证队列
→ 用户填 Outcome → 评分（Brier/LogLoss/校准/Sharpness/Skill vs Null）
→ run_learning_after_verify：归因 → 假设落库 → Shadow 样本 → 规则统计 → 可靠度回喂 Fusion 权重
```

调度器（`app/scheduler.py`，APScheduler，3 个 job）：
- 23:30 Reality 更新 → 23:40 每日管线 → 21:00 验证提醒

---

## 10. 十项验收标准（tests/test_acceptance.py）

| ID | 含义 |
|---|---|
| PRED-01 | 每天自动生成 ≥3 条正式预测 |
| PRED-02 | 每条可证伪/有概率/有窗口/有成败标准 |
| FREEZE-01 | 预测发布后不可覆盖 |
| VERIFY-01 | 到期自动进验证队列 |
| VERIFY-02 | 自然语言可映射为 Outcome |
| SCORE-01 | Brier / Calibration / Skill vs Null |
| ADV-01 | 模糊预测不能过 Gate |
| ADV-02 | 失败预测不能隐藏 |
| LEARN-01 | 能定位哪个系统/规则/尺度导致错误 |
| EXP-01 | 能跑 Reality Only / Metaphysical Only / Fusion / Null 对照 |

---

## 11. 已修复的历史坑（别重踩，别回退）

1. **httpx 走系统代理挂死** → `trust_env=False`（第 4.3 节）。
2. **中转站 SSE 流式** → 兼容解析 `data:` 行（第 4.4 节）。
3. **`response_format=json_object` 挂起** → 改 prompt 约束 + 宽容 JSON 解析。
4. **SQLite 内存库 + TestClient** 需 `poolclass=StaticPool`，否则建表/请求两套空库报 `no such table`。
5. **SQLModel 共享 JSON 列**：被多表继承的基类字段不能用 `sa_column=Column(JSON)`（同一 Column 实例冲突），必须 `sa_type=JSON`。
6. **FastAPI 路由顺序**：静态路径 `/predictions/history` 要定义在动态 `/predictions/{id}` 之前。
7. **lunar-python 十神方法**是 `getYearShiShenGan/Zhi`（天干/地支两套），不是 `...Gang`。
8. **冻结哈希**直接对 freeze_payload 算，从其他表重建会因随机 signal_id 假阳性。
9. **iztro-py 时辰索引**非小时（第 4.5 节）。
10. **qiyovo 中转站间歇软错误**（2026-08-30）——HTTP 200 + body `{"code":502,"message":"..."}`。`OpenAICompatibleProvider.complete()` 已检测并重试，**别把这段防护删了**。
11. **出生档案接口只有 create 没有 update**（2026-08-30）——已补 `PUT /api/users/{id}/profile`。
12. **`_score()` 不写 `source_types` → 可靠度矩阵 by_source 永远为空，学习闭环结构性断链**（2026-08-31 第二轮治本修复）：验证评分时必须从 SignalRecord 归集该预测的信号源/规则落进 `prediction_scores`，否则「Skill 回喂 Fusion」永远空转。
13. **`fusion_weights()` 对 skill=None 源曾返回 1.0（全可信）**——违反禁止 6「初始只允许弱先验」，是「噪声偶然偏离 Null 造出假 edge」的根因。现为 0.5 弱先验下限，并对全部已知源显式给权重。配合**校准三阶段**（config `MIN_CALIBRATION_SAMPLES=5` / `MIN_FORMAL_SAMPLES=20`）：cold 不出术式、explore 弱先验参与但只产 RESEARCH 留痕、formal 正式预测。测试默认两个门槛都为 0（conftest 关闭），专项测试在 `tests/test_calibration_gate.py`（7 例）。
14. **`datetime.utcnow()` 已全面弃用**——统一用 `app.utils.utcnow()`（naive UTC，语义不变）。新代码别再写 `datetime.utcnow()`。
15. **qiyovo reasoning 模型（deepseek-v4-flash）会整段长时间不可用**（2026-09-02 实测：连续 5×180s 全部超时；另一次小请求直接 500），而 cheap 层（minimax-m3）正常。`fortune.py._complete_with_fallback()`：批示类调用 reasoning 只试 2 次（快失败，别让用户等 5×180s），失败自动回退 cheap，双失败时错误信息同时带两层原因、确定性盘面照常返回。只改实例属性（`get_provider` 每次新建，不影响管线调用方）。**别把 reasoning 重试数调回去**，否则 UI 点「重算批示」最长卡一刻钟。
16. **Gate 语义 vs 详批叙事必须分层**（2026-09-03）：长文案进 `description` 会被对抗 Gate 团灭——DefinitionAttack 禁词（"运势/贵人/小人/桃花/气场/能量/福报/机缘/可能/也许/大概/相关"…）在 description+criteria 命中即 REJECT；Vagueness/Definition 的 WARN 也会被当 REWRITE 丢候选。踩过的词：`运势锦囊→幸运参考`、`可能形态→常见情景`、`贵人相助→获得实质帮助`、`桃花提示→情缘提示`、`咸池桃花→咸池引动`。架构解法：**冻结仓的描述只放「何时+何事」短声明**（如「9月4日（周五）遇到心动的缘分。」），多行详批（常见情景/多法印证/建议/注意/幸运参考）是**读侧确定性重建**（`cross_engine.narrative_for_record()`，由 event_type + SignalRecord + 当日历算现拼），不进冻结载荷、不影响哈希、天然免疫 Gate。给叙事/本体写新文案时先对着 `app/adversarial/attacks/deterministic.py` 里的禁词表自查。
17. **`iztro` `horoscope` 的 palace_names 是 12 宫中文名列表**（按本命宫序），转宿主本命宫要 `.index(...)` 映射回索引；流年干支是 `'gengHeavenly'` 这种 camelCase 枚举字符串，需 `_HEAVENLY_ZH` 映射回天干再查四化表。
18. **exe 数据目录在 `dist/data/`（spec datas 里 `("data", "data")`）**，所以清库/补数据要同时处理 repo 的 `data/xuanmirror.db` 和 `dist/data/xuanmirror.db` 两个副本。打包后浏览器若显示旧页面，多半是旧 exe 进程没杀或标签页缓存——换端口验证或给 URL 加 `?fresh=1` 强刷。
19. **研究期连点两次「生成预测」会重复冻结同一批选题**（2026-09-03 修）：`pipeline._run_research()` 冻结前按 `(event_type, time_scale, window_start.date())` 对在库（FROZEN/RESEARCH/VERIFY_REQUIRED）样本去重，跳过并在 notes 注明「去重：跳过 N 条」。**time_scale 必须在键里**——同一事件在日/周/月是三条独立可证伪声明，缺了它月度样本会被周样本误杀（教训来自一次测试失败）。
20. **pip 版 opencv 不带 haarcascade xml**（2026-09-03）：`cv2.data.haarcascades` 目录只有 `__init__.py`，`CascadeClassifier` 静默为空 → 面相检测假阴性。修复：`app/core/face/assets/` 内置一份 `haarcascade_frontalface_default.xml`（官方 master 副本，spec datas 已收），加载后查 `cascade.empty()`，空则诚实降级 detected=False。
21. **手写 64 卦名表必有键序错位**（2026-09-05，round 10 对抗性审计战果）：梅花引擎 `GUA_NAMES` 手写表有 **9 条上下卦颠倒**（如 `(离,乾)` 误作「火天大有」，实为「天火同人」；`(坎,乾)` 误作「水天需」，实为「天水讼」）且仅覆盖 60 卦——等于梅花信号里 9/64 的卦名是错的，叙述层却无从察觉。治本：`GUA_NAMES` 改由六爻 `HEXAGRAMS`（pattern→name 权威表）程序化生成，单一事实源。**教训：凡是「手抄 N 项全量表」都要配一张程序化对照测试，或直接程序化生成**；`bian_gua.upper/lower` 误用本卦字段的同源 bug 一并修复，engine 版本升至 `meihua-0.2.0`。回归固化在 `tests/test_zhouyi_canon.py::test_meihua_gua_names_match_canonical_table`。
22. **爻题推导规则**（2026-09-05）：初爻/上爻用「初九/上九」式，**二至五位用「九二/九三」式**（九/六在前）——第一版写成「二九」被测试当场拦下。固化在 `test_yao_positions_rules`。
23. **历法常量表漏字**（2026-09-05，round 11 审查战果，★★★ 影响每天）：`calendar/core.py` 的 `DIZHI_WUXING` 只有 11 字（漏戌的「土」）——戌时（19-21 点）五行**静默算错**，亥时（21-23 点）`DIZHI_WUXING[11]` IndexError → CalendarCore 降级 → 六爻降级分支又缺 `hour_branch` 直接 UnboundLocalError。**每天 21:00-22:59 所有排盘全废**，且无任何告警。修复 + 常量长度回归测试（`test_calendar_dizhi_wuxing_complete`）。教训：**所有「干支序 → 属性」的紧凑常量串都要配长度断言**。
24. **验证闭环判定语义**（2026-09-05，round 11）：修复前快捷裁决 A/B/C 只给 Collector 做歧义检测，最终 outcome 由三方 LLM Judge 对**可能为空的** user_reply 瞎猜的均值顶替；三方 Judge 全挂（confidence=0）恰好零分歧 → 以 0 置信度静默记「未中」并进评分。修复：快捷 A/B/C 直通落 outcome（用户是判定权威，不经 LLM）、D → WAITING_USER 不落结果、`from_verdicts` 失败感知转人工、已批复再 verify → 409（C-003 后端强保）。固化在 `tests/test_review_fixes.py`（13 项）。

---

25. **奇门定局跳过中气**（2026-09-05，round 14 回测战果，★★★ 全年约一半日子整盘作废）：`active_jie` 用 `lunar.getPrevJie()` —— lunar-python 的「节」只有十二节（立春/惊蛰/白露…），**不含十二中气**（雨水/春分/夏至/秋分/冬至…）。每逢中气统领的时段，定局会跳回上一个「节」错一档（如 1992-10-03 在秋分段，被判成白露上元九局，正确为秋分上元七局）。必须用 `getPrevJieQi/getNextJieQi`（24 节气全表）。教训：**外部库的「jie」字样 API 未必等于「节气」，要先确认枚举范围**。
26. **奇门断法三处传统口径错误**（2026-09-05，round 14，qimen-0.2.0）：①凶门表把杜/景也算凶（正确：三吉开休生/三凶死惊伤/杜景中平）；②伏吟/反吟按「单宫天干==地干/互克」判定，几乎日日检出（正确为**全盘级**：八宫天干全同=伏吟、全落对冲=反吟）；③「门迫」实现成宫克门（正确为门克宫）且不限宫位。另 zhouyi-0.2.0：亨/利/无咎/无悔等高频套话降权至对称阈值 ±0.5 之下（「元亨利贞」不再恒出正向——回测里 94% 命中全是灌水，零信息量）。奇门主断改为「日干落宫(人)×时干落宫(事)」生克，门/格局为修正，相左时弃权。

27. **年柱双口径**（2026-09-06，round 15 回测发现，非 bug 而是必须知道的口径差）：CalendarCore 同时输出 `year_ganzhi`（农历年，正月初一切换，供老黄历/时间起卦）与 `bazi.year`（八字年柱，立春切换，供四柱）。生日在立春与正月初一之间时两者相差一年（如 C罗 1985-02-05：八字乙丑/农历甲子），**各自口径下都正确**。核对/开发时务必先问「要用哪套年」，勿把农历年当八字年柱（回测曾因此误报）。

28. **固定时辰起卦的方向偏斜**（2026-09-06，round 16 审计战果）：梅花/六爻时间起卦的上下卦组合由「年月日(+时)」决定，固定时辰（如全部 12:00 或管线默认 00:00）会把上下卦数差锁死，梅花引擎分布本均匀（10000 次普查 41% 负）但固定午时采样 77% 负、产品子时同样偏斜。修复：pipeline 的 AdapterQuery 传真实本地时辰；回测采样逐事件伪随机时辰。教训：**时间起卦类引擎的「时辰」是分布参数，任何固定采样都会引入方向偏斜**。另：liuyao 旺衰打分实测均值 -0.4（月克/日克权重不对称），已重定心；bazi 强弱阈值 ±2 过宽曾使清晰盘永久沉默，收紧至 ±1.5。

29. **相法测量假数据与 mediapipe 打包**（2026-09-06，round 18 数量级核验战果）：①面相旧实现「三庭/五眼/眉眼/鼻唇颌」全是 Haar 框等分**写死常数**——传谁的照片解读都是「三庭匀称」，千人一面假测量；现改 MediaPipe FaceLandmarker 真关键点（facemesh 不可用时只出真可测的框宽高比，宁缺毋假）。②掌纹 `palm_width_ratio=掌宽/图宽` 随取景漂移无意义，改 Hands 真掌宽/掌长（0.6~0.8）；`_measure_line_group` **投影轴写反**（单条线 length_ratio 恒 0）已修，阈值以合成纹图核验。③mediapipe 1.x **移除 solutions API**，须用 Tasks API + .task 模型文件（Apache 2.0，已入 `app/core/face/assets/`）；**spec 的 excludes 里曾排除 matplotlib**——mediapipe tasks 链路 import matplotlib.pyplot，被排除即 exe 内 ModuleNotFoundError（hiddenimports 打不过 excludes），已从 excludes 移除并显式收集。教训：**数量级核验要测的是「测量数学层」（canonical 输入→已知输出），别只测引擎不测测量**；excludes 名单会静默杀死 hiddenimports。

## 12. 待优化方向（给接手者的建议，按优先级）

> 这些是「下一步该干嘛」的明确清单。前 3 项是真正的短板，其余是打磨。

1. **【高】开启调度器跑真实闭环**：`.env` 里 `SCHEDULER_ENABLED=true`，让系统 23:40 真跑每日管线，开始积累真实样本。目前样本量为 0，学习闭环/可靠度矩阵/校准曲线全是「空转」状态——没有真实数据，整个系统的核心价值（自我校准）体现不出来。

2. ~~**【高】LLM 调用并发化**~~ ✅ 已做（2026-08-31 第三轮）：管线重排为「确定性先行」——研究期（cold/explore）**零 LLM**（扫描走 Ontology，秒出）；正式期只对预算入选候选并发润色（ThreadPoolExecutor×4，每 worker 独立 Session），LLM 失败回落确定性版本。注意：**概率权威在融合侧**，CandidateAgent 自报的 probability 一律丢弃（C-005），改这条前先读诊断报告第 10 节。

3. **【高】掌纹/面相无测试样例**：这两个 CV 引擎需要 `AdapterQuery.image_path` 传本地照片才产出信号，目前测试没覆盖真实图片路径。建议造 1-2 张样例图（手部/人脸）进 `tests/fixtures/`，补真实 CV 的 golden case。

4. ~~**【中】清理 deprecation warnings**~~ ✅ 已做（2026-08-31）：`datetime.utcnow()` 全部替换为 `app.utils.utcnow()`（21 个文件），警告 1369 → 1（仅余 FastAPI TestClient 自身提示）。

5. **【中】前端新功能入口核对**：后端已实现 Future Tree / Counterfactual / 双盲实验 / Obsidian 导出 / 报告，但前端只补了 Future 页。核对 `frontend/src/pages/` 是否缺「实验」「导出」「报告」的入口，需要就补页面 + `api/client.ts` 的调用。

6. **【中】文档与代码一致性**（用户很看重）：README 声称 37 表/21 Agent，实际核对下来一致；但 `rules/` 只有 bazi.yaml 一个示例、`prompts/` 只有 constitution.txt，Rule Registry 和 Prompt 版本库还很空。需要的话按方案补全。

7. **【低】数据库 V1→V2**：方案第 44 节说 V2 迁 PostgreSQL（`app/database.py` 里已留注释）。当前 SQLite 够用，不是急事。

8. **【低】远端 codex/* 分支**：远端还有 5 个 `codex/*` 分支未删（用户当时说「清空」只清了 main），要删需用户确认。

---

## 13. 安全边界（接手者务必遵守）

- 这是**术数 + 个人预测实验平台，不是经科学验证的预知系统**，代码注释和文档里一直强调这点，别改成「算命大师」话术。
- 系统不得：以术数诊断疾病、预测死亡日期、替代医生/律师/财务、鼓励高风险下注、因面部特征推断敏感人格。
- 面部/掌纹/出生信息是高敏感数据，**本地优先**（`ENABLE_CLOUD_VISION=false`）。
- **宪法（docs/CONSTITUTION.md）是最高准则**，任何改动不得违反 C-001~C-007。
