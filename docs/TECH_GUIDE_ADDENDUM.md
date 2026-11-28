# 赛马娘 SO 插件 · 2.33.2 实测突破与 `ci_patch12..19` 技术补充文档

> 说明：本文件为 `FULL_TECH_GUIDE.md` 的外部增量补充，记录 2026-11-28 在 `jp.co.cygames.umamusume` **2.33.2** 上新增实测锁定的偏移、架构升级、补丁演进与运行时热控说明。

---

## 1. CI 补丁脚本职责续表（`ci_patch12.py` ~ `ci_patch19.py`）

| 脚本 | 标签 / Commit | 职责摘要 |
|---|---|---|
| `ci_patch12.py` | — | `AlignedJmpBuf` + `SIGSEGV_RECOVERY_TID` + `read_inline_obscured_int_array` + IL2CPP 线程 attach |
| `ci_patch13.py` | `782a6d18` | 链式 `sigaction` 信号转发（修故障 #8）+ 精简开机 14 个诊断 hook 为 6 个核心 hook（修故障 #9） |
| `ci_patch14.py` | `f69f5350` | 修 `read_summary_inner` `_charaEffectIdArray`（故障 #10）+ 补齐 `cardId/persons/personDistribution/isIll` |
| `ci_patch15.py` | `v3.29.1-p15` (`1a5f42ca`) | 补读训练等级 `Chara+736` + 重复帧幂等保留权重 + 降级态保稽古 `200` |
| `ci_patch16.py` | `51ddfe2b` | ① 回合同步 `mt_peek_turn_fast` + Condvar 严禁跨回合复用旧权重；② 直读 `Chara+1232 <ScenarioFesta> -> +16 <DataSet>` 接通 2.33.2 真实 `acts/persons/friend/checkPoints/kensan`；③ 开启轻量 MCTS `Search(n=128)` + `distilled.toml` |
| `ci_patch17.py` | `v3.29.1-p17` (`f7f21237`) | 扩展 `MT_W` 至 15 槽全统筹（`150101..150105` / `150120..150124` / `150106..150110`），首选非训练时保留训练 `1..40` 相对排序 |
| `ci_patch18.py` | `v3.29.1-p18` (`b223a5dd`) | 补读 `WorkSingleModeData+208 (_raceHistoryInfoList)` → `baseGame.raceHistory` 根治故障 #12（T27–T76 `career_failed` 截断 MCTS）+ T2 `advice_pending` 过渡保护 |
| `ci_patch19.py` | `v3.29.1-p19.5` (`f551e754`) | 教诲附着 (`AttachAdviceAutoPlayProxy.OnClickDecide`，覆盖全部 6 窗口 `T=2/11/23/35/47/59`) + 教诲転成 (`FusionAdviceAutoPlayProxy.AutoFusionAdvice`) + 全事件选项 (`StoryViewProxy.TryChoice` + `WorkSingleModeData+440 EventChoiceRewardDict` 实时构建 `baseGame.story`) 三合一接管 + `ai_search.txt` 热开关与高算力真机上限扩容（`search_n ≤ 16384`, `group_size ≤ 1024`, `threads ≤ 12`, `wait_ms ≤ 15000`） |

---

## 2. 原交接清单（`FULL_TECH_GUIDE.md` §12）逐项清零状态

| 原编号 | 项目 | 2.33.2 实机破解结论 | 对应补丁 |
|---|---|---|---|
| #1 | Festa 真实 `acts`（此前误以为 2.33.2 废弃） | `try_get_scenario_obj()` 误取了 `Chara+1160` (`WorkScenarioURA`)；真实 `<ScenarioFesta>` 在 **`WorkSingleModeCharaData + 1232 (0x4d0)`**，其 **`+16 (0x10)`** 即为实时有效的 `WorkSingleModeScenarioFestaDataSet`（零新增 hook） | `ci_patch16.py` |
| #2 | 非训练 omakase 编号 | 汇编 + MDB + 单变量实测全锁定：`150106` 休息/合宿休息、`150107` 友人出行、`150108` 普通外出、`150109` 保健室、`150110` 比赛（详见 [OMAKASE_NON_TRAINING_ENTRIES.md](./OMAKASE_NON_TRAINING_ENTRIES.md)） | `ci_patch17.py` |
| #3 | 教诲附着 / 転成接管 | 嵌套代理 `AttachAdviceAutoPlayProxy.OnClickDecide`（覆盖全部 6 窗口 `T=2/11/23/35/47/59`；`proxy+16 -> ctrl+96 vm -> vm+48 entity -> +16 owned`，按 `(epoch, window)` 去重后在提交前调 `DetachAllAdvice` + 5× `UpdateAttachAdviceQuick(1..=5, ent)` + `UpdateAttachAdviceView`）与 `FusionAdviceAutoPlayProxy.AutoFusionAdvice`（`proxy+16 -> ctrl+48 vm`，调 `ResetAllFusionTargetAdviceVoList` + `UpdateFusionTargetAdviceVoList` + `UpdateOnChangeFusionAdvice`） | `ci_patch19.py` |
| #4 | 全事件选项接管 | 嵌套代理 `Gallop.StoryViewController/StoryViewProxy.TryChoice(int index)`（仅 omakase 调用，不干扰手动点击）；直读 `WorkSingleModeData + 440 (0x1b8)` `EventChoiceRewardDict`（Entry 步长 `0x28`：`+0x08 ObscuredInt key`，`+0x20 EventChoiceReward*`）实时拼装 `baseGame.story` 喂给 `matsuri`（ARM64 实测 `GainParameterType`：`1..5`=五维、`10`=体力、`11`=体力上限、`20`=干劲、`30`=PT、`40`=粉丝、`51..55`=上限；`text_data cat=394` 61 条 `displayId`） | `ci_patch19.py` |
| #5 | 完整对局与稳定性验证 | Run #5（`v3.29.1-p18`）测试卡组 `48,616 → 52,404 (UB6)`；大号系列 B：Baseline `60,749 (UA6)` → Run B1（`p19.2`, `search_n=128`）`69,123 (US5)` → **Run B2（`p19.4`, `search_n=2048` + 6/6 教诲全窗口）`73,526 (US8)`（`+12,777`）**，全程 78 回合 **`decided=78 / skipped=0 / failed=0 / tombstones=0`** | `ci_patch18.py` / `ci_patch19.py` |

---

## 3. 新增 2.33.2 实测偏移与运行时热控

### 3.1 实测偏移汇总
- **`WorkDataManager + 96 (0x60)`**：`<SingleMode>` (`WorkSingleModeData`)
- **`WorkSingleModeData + 68 (0x44)`**：`_totalTurnNum` (`ObscuredInt`, 1-based `1..=78`)
- **`WorkSingleModeData + 88 (0x58)`**：`<Character>` (`WorkSingleModeCharaData`)
- **`WorkSingleModeData + 112 (0x70)`**：`<HomeInfoData>` (`WorkSingleModeHomeInfoData`)
- **`WorkSingleModeData + 208 (0xd0)`**：`_raceHistoryInfoList` (`List<RaceHistoryInfo>`)；每项 `+16` `_turn`（1-based `ObscuredInt`，减 1 转 0-based 填 `baseGame.raceHistory`）
- **`WorkSingleModeData + 440 (0x1b8)`**：`EventChoiceRewardDict` (`Dictionary<ObscuredInt, EventChoiceReward>`)；`EventChoiceReward+16` `_branchRewardArray` → `EventChoiceBranchReward+16` `_gainParamArray` → `EventChoiceRewardGainParam`（`+16 _displayId`、`+36/_56/_76 _effectValue0..2`，均为 `ObscuredInt`）
- **`WorkSingleModeCharaData + 16 (0x10)`**：`_id` (`single_mode_chara_id`，局号)
- **`WorkSingleModeCharaData + 736 (0x2e0)`**：`<TrainingLevelInfoArray>`
- **`WorkSingleModeCharaData + 1016 (0x3f8)`**：`_evaluationList`；友人位（`1..=6` 且 `+112 CharaId >= 9000`）：`+56 IsOuting`（12 字节 `ObscuredBool`，`+0 key:u8, +4 hidden:i32`，`181=false, 213=true`）、`+68 StoryStep`（`ObscuredInt`, `0..5` → `friend_outgoingUsed`）
- **`WorkSingleModeCharaData + 1232 (0x4d0)`**：`<ScenarioFesta>` → **`+16 (0x10)`** `WorkSingleModeScenarioFestaDataSet`（`+16 FestaInfo`、`+24 CommandInfoArray`、`+32 EvaluationInfoArray`、`+40 AdviceInfoArray`、`+48 ActInfoArray`、`+56 CheckPointResultArray`）

### 3.2 运行时热控文件（无需重打包 / 无需重启）
路径：`/sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/ai_search.txt`（`ci_patch19` 起支持 2 秒热加载）：
```ini
# 随时关闭全部 AI 接管（训练 / 教诲 / 事件全部透传原版 omakase，方便跑纯原版基线）
enabled=true
# 决策模式：search（轻量 MCTS）或 hand（纯蒸馏静态打分）
mode=search
# MCTS 模拟次数（默认 128；v3.29.1-p19.5 起可调 16..16384，0=Hand）
search_n=128
# UCB 步长（默认 32；可调 8..1024）
group_size=32
# 搜索线程数（默认 4；可调 1..12，自动不超过设备可用核数；改此项需重启游戏）
threads=4
# 主线程换回合等待上限毫秒（默认 1500；可调 100..15000）
wait_ms=1500
```

### 3.3 Hook 预算与关联文档
- **总 Hook 数**：`ci_patch13` 精简至 6 个，`ci_patch19` 新增 3 个低频弹窗 hook 后合计 **9 个**（远低于故障 #9 撑爆 Dobby 4KB 页的 20+ 阈值）。
- 运行时故障取证：[RUNTIME_FAULTS_LOG.md](./RUNTIME_FAULTS_LOG.md)
- 对局评分对照：[OMAKASE_BENCHMARK_LOG.md](./OMAKASE_BENCHMARK_LOG.md)
- 非训练指令手册：[OMAKASE_NON_TRAINING_ENTRIES.md](./OMAKASE_NON_TRAINING_ENTRIES.md)
