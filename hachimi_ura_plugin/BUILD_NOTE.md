# 构建触发记录

本文件只用来触发 `build-so` workflow（它的 `paths` 过滤只监听 `hachimi_ura_plugin/**`），
不参与编译，可随时增删。

## 2026-11-27 · ci_patch10（dataset 缺失给空 festa）

### 进度线

```
matsuri 会话        ready=true    ✅ 7.3MB 在后台线程解析通过，卡组 6 张过门禁
帧构造              dataset_null  ❌ 卡在这
```

`WorkSingleModeScenarioFesta` 只有 1 个字段（`+16 DataSet`），property 与 backing field
都是 null —— 2.33.2 把这套 DataSet 在运行时停用了，数据搬到
`Gallop.SingleMode.ScenarioFesta.*` 的 Repository/Entity：

```
ActEntity        +16 ActType  +24 ActMemberCharaIdList  +32 MaxActTrainingLevel
                 +36 ActTrainingState  +56 CurrentActGauge  +60 LevelUpByFriendOuting
ActListEntity    +16 ActEntityList（IReadOnlyList<ActEntity>）  +24 ActTrainingStep
TrainingCommandEntity  +16 CommandId  +24 _gainActGaugeAmountDict
```

按 `CreateActEntityList(maxActLevel, actInfoArray, partnerArray, ...)` 的签名看，
旧的 `ObscuredSingleModeFestaActInfo` 数组还在，只是不再挂在 DataSet 上。

### 两个崩溃各算谁的

| 崩溃 | 原因 |
|---|---|
| 开自动后闪退（`matsuri_data` 刚落盘那次） | `Data::init` 解析 7.3 MB 跑在渲染线程 → 已搬到 worker |
| `socket hang up`（刚这次） | 我在 HTTP 线程调静态 `ActListRepository.Get()` → 游戏进程被杀 |

后者不再尝试，静态方法在非主线程调用这条路封掉。

### 这一版的取舍

`acts: []` 是 matsuri 的合法缺省（`restore_acts` 注释：`level == 0` 表示本帧未给，
保持默认 Lv1），不是假数据。代价写进帧的 `notes: ["dataset_missing"]`：

- 演目槽 / 演目 Lv 缺失 → 稽古相关估值偏低
- **补齐真实 acts 之前，不能拿稽古决策当真**

## 已确认的映射（游戏 2.33.2 / setting 1501）

```
150101 → 速度    Train(0)
150102 → 耐力    Train(1)
150103 → 力量    Train(2)
150104 → 根性    Train(3)   排除法
150105 → 智力    Train(4)
150120..150124 → ActTrain(0..4)  演目下标，与 acts[].train 一致
```

## 支援卡读法（已实测）

```
元素是引用（seq_item 取指针）；三个字段都是 ObscuredInt、间距 20 字节：
  +16 position   +36 supportCardId   +56 limitBreak
卡号 = supportCardId * 10 + limitBreak
实测 6 张：303224 303114 302754 302894 303044 303232
```