# omakase 非训练指令权重入口全表（`150106–150110` 汇编 + MDB 双重验证与单变量测试手册）

- 日期：2026-11-28
- 游戏：`jp.co.cygames.umamusume` **2.33.2**（arm64，Festa 骏大祭 `setting_id = 1501`）
- 证据来源（全部实测自雷电 14 `PID 1465` 运行时）：
  1. `SingleModeAutoPlayWeightRepositoryBase` 6 个 `Get*BaseWeight` 方法的 ARM64 实时反汇编（`/il2cpp/disassemble`）；
  2. 游戏本体 `/data/data/jp.co.cygames.umamusume/files/master/master.mdb` 的 `omakase_command` / `omakase_base_pt` / `omakase_outing_base_pt` / `omakase_condition_set` / `omakase_filter`。

---

## 1. 完整编号总表（ARM64 汇编逐条锁定）

`SingleModeAutoPlayWeightRepositoryBase` 中 6 个非训练取权方法均为固定 8 条 ARM64 指令，逻辑完全统一：
先调 `GetSettingId(this, 1)` 拿到 `1501`，再算 `setting_id * 100 + 偏移`，最后尾调 `GetBaseWeight(this, commandIdType)`。

| `commandIdType` | 方法名（`SingleModeAutoPlayWeightRepositoryBase`） | ARM64 关键立即数指令（实测） | 对应游戏指令 | `matsuri` `Action` / tag | `master.mdb` 基础权重 |
|---|---|---|---|---|---|
| **`150106`** | `GetHolidayBaseWeight`（`+0x00`）<br>`GetSummerCampBaseWeight`（`+0x28`） | `mov w8, #100`<br>`orr w9, wzr, #6` (`e9 07 1f 32`)<br>`madd w1, w0, w8, w9` | **休息（お休み）** & **合宿休息（夏合宿お休み）**（两者共用 `150106`！） | `Action::Rest` (`3001`) | `10` |
| **`150107`** | `GetOutingBaseWeight`（`+0xa0`） | `mov w8, #100`<br>`orr w9, wzr, #7` (`e9 0b 00 32`)<br>`madd w1, w0, w8, w9` | **友人 / 团队卡出行（友人お出かけ）** | `Action::FriendOuting` (`3003`) | `20` |
| **`150108`** | `GetOutingTrainingCharaBaseWeight`（`+0xc8`） | `mov w8, #100`<br>`orr w9, wzr, #8` (`e9 03 1d 32`)<br>`madd w1, w0, w8, w9` | **育成马娘普通外出（通常お出かけ）** | `Action::NormalOuting` (`3002`) | `0` |
| **`150109`** | `GetHospitalBaseWeight`（`+0x78`） | `mov w8, #100`<br>`mul w8, w0, w8`<br>`add w1, w8, #9` (`01 25 00 11`) | **保健室（治病）** | `Action::Clinic` (`3004`) | `0` |
| **`150110`** | `GetRaceBaseWeight`（`+0x50`） | `mov w8, #100`<br>`mul w8, w0, w8`<br>`add w1, w8, #10` (`01 29 00 11`) | **比赛（レース）** | `Action::Race` (`3000`) | `10` |

> 附：其余已知编号对照：
> - `150101..150105` = 速 / 耐 / 力 / 根 / 智 普通训练（基础分 `10`）
> - `150120..150124` = 5 大演目稽古（基础分 `200`）

---

## 2. 各入口的条件加分与硬过滤（`master.mdb` 原表）

### 2.1 `150106`（普通休息 / 合宿休息）
- **硬过滤（`omakase_filter` #15011）**：`condition_set_id = 211`（`condition_type = 2, value1 = 100, value2 = 999`）→ **体力 `>= 100` 时直接被过滤，不会进入候选**。
- **条件加分（`omakase_base_pt`）**：
  - `cond 201`（体力 `31..50`）：`+60`
  - `cond 202`（体力 `0..30`）：`+90`
  - 体力 `51..99` 时加分为 `0`（仅基础分 `10`，但**仍在候选中，改大权重可触发**）。

### 2.2 `150107`（友人 / 团队出行）
- **硬过滤（`omakase_filter` #15012）**：同样受 `cond 211`（体力 `>= 100`）过滤；且**必须先解锁友人出行且仍有剩余段数**才会进候选。
- **为什么之前同伴测 `150107=10000` 没触发休息（`WEIGHT_OVERRIDE_IMPL.md` §5.2）**：
  1. `150107` 本来就不是休息，而是**友人出行**；
  2. 如果当时未解锁友人出行、友人 5 段已用完、或处于体力 `>= 100` / 合宿期，候选里根本没有 `150107`，写了 `10000` 也不会命中。
- **条件加分（走独立表 `omakase_outing_base_pt`，`omakase_base_pt` 中为 0 行）**：
  - 通用（`chara_id=0, scenario_id=0`）：体力 `31..50` `+60`、体力 `0..30` `+90`。

### 2.3 `150108`（育成马娘普通外出）
- **硬过滤（`omakase_filter` #15013）**：体力 `>= 100` 过滤；合宿期不可用。
- **条件加分（`omakase_base_pt`）**：
  - `cond 201`（体力 `31..50`）：`+60`
  - `cond 202`（体力 `0..30`）：`+90`
  - 基础分 `0`（原版通常不爱点普通外出，但拉大权重可测试是否立刻去普通外出）。

### 2.4 `150109`（保健室）
- **前置条件**：马娘必须实际带有可治病种（142 表 `1..6`：熬夜 / 懒惰 / 肌荒 / 变胖 / 偏头痛 / 不擅长练习），否则保健室按钮本身不可选、`150109` 不进候选。
- **条件加分（`omakase_base_pt`）**：`cond 203..208`（`condition_type = 9, value1 = 1..6`），每命中 1 种病 `+100`。

### 2.5 `150110`（比赛）
- **前置条件**：当回合存在可报名的非目标自选赛（目标必跑赛不由普通选训权重决定）。
- **条件加分（`omakase_base_pt`）**：
  - `cond 303`：`+1200`（必跑/高优先赛事）
  - `cond 301 / 308 / 309`：分别 `+20 / +10 / +10`（粉丝门槛与距离/场地适性）
  - `cond 310`：`-999`（适性不足扣分）
- **过滤器（`omakase_filter` #15001–15005）**：受 omakase 计划中的赛程/适性/粉丝过滤开关控制。

---

## 3. 单变量实测验证步骤（直接照抄就能验）

覆盖表路径（优先级高于 `matsuri` 和原值，2 秒热加载，**无需重启游戏**）：
```text
/sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/weight_override.txt
```

### 测试纪律
1. **每次只写一行 `<编号>=50000`**，测完立即改下一行或清空；
2. 必须在**满足该指令前置条件**的回合测（例如体力 `< 100`、非目标赛、非合宿测外出）；
3. 每测一个号，核对三个指标：
   - `/api/ai/override`：`applied` 是否上涨（确认覆盖表读到了）
   - `/api/ai/weightcalls`：对应 `arg` 是否返回 `50000`（确认候选里有该编号且已替换）
   - 游戏界面：自动育成实际点下去的动作是否与下表一致。

### 逐项验证命令（在电脑 PowerShell 执行）

```powershell
$adb = "adb" # 按本机 adb 路径替换
$ovr = "/sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/weight_override.txt"

# 1) 测 150106（预期：休息 / 合宿休息；需体力 < 100）
& $adb -s emulator-5554 shell "echo '150106=50000' > $ovr"

# 2) 测 150107（预期：友人出行；需已解锁吾妻出行且有剩余次数、体力 < 100、非合宿）
& $adb -s emulator-5554 shell "echo '150107=50000' > $ovr"

# 3) 测 150108（预期：马娘本人普通外出；需体力 < 100、非合宿）
& $adb -s emulator-5554 shell "echo '150108=50000' > $ovr"

# 4) 测 150109（预期：保健室；需马娘生病 1..6）
& $adb -s emulator-5554 shell "echo '150109=50000' > $ovr"

# 5) 测 150110（预期：出赛；需当回合有可报名的比赛且未被 omakase 计划过滤）
& $adb -s emulator-5554 shell "echo '150110=50000' > $ovr"

# 检查命中状态（随时执行）
& $adb -s emulator-5554 shell "curl -s http://127.0.0.1:18765/api/ai/override; echo ''; curl -s http://127.0.0.1:18765/api/ai/weightcalls; echo ''"

# ★ 测完务必写空覆盖表，恢复正常模式（不要直接 rm -f，ci_patch3 读不到文件时会保留内存旧表）
& $adb -s emulator-5554 shell "printf '# cleared\n' > $ovr"
```

---

## 4. 实测记录区（2026-11-28 雷电 14 单变量实测结果）

| 编号 | 汇编归属 | 实测回合条件 | `override.applied` | `weightcalls` 返回 `50000` | 游戏实际动作 | 结论 |
|---|---|---|---|---|---|---|
| `150106` | `GetHolidayBaseWeight` / `GetSummerCampBaseWeight` | 经典年 5 月后半（`T=33`），体力 `97/108`（注：`T=32` 体力 `108/108` 时未查询，同步验证 `cond 211` 体力 `>=100` 硬过滤） | `25 → 26` | ✅ `150106 -> 50000` | **休息**（触发「休息はバッチリ！」体力 `97→108`） | ✅ **实测锁定** |
| `150107` | `GetOutingBaseWeight` | 经典年 4 月后半（`T=31`），体力 `88/108`，友人出行已解锁且有剩余 | `23 → 25`（评估 2 次） | ✅ `150107 -> 50000` | **友人出行**（触发 SSR 吾妻出行第 3 段「共に、今を辿る」，体力 `88→108`） | ✅ **实测锁定** |
| `150108` | `GetOutingTrainingCharaBaseWeight` | 经典年 4 月前半（`T=30`），体力 `83/108`，非合宿 | `22 → 23` | ✅ `150108 -> 50000` | **育成马娘普通外出**（触发卡拉OK + 抓娃娃机） | ✅ **实测锁定** |
| `150109` | `GetHospitalBaseWeight` | 本局未生病（`isIll=false`，保健室按钮置灰未进候选） | — | — | 未生病不可触发；其余 4 项均已实测锁定 + 汇编/MDB 六病条件交叉吻合 | ⚠️ **待生病回合补测**（逻辑已唯一锁定） |
| `150110` | `GetRaceBaseWeight` | 经典年 3 月后半（`T=29`），体力 `98/108`，存在可选 GII/GIII 赛 | `21 → 22` | ✅ `150110 -> 50000` | **出赛**（立即进入「レース一覧」报名出走 GII スプリングステークス） | ✅ **实测锁定** |
