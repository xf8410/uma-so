# 构建触发记录

本文件只用来触发 `build-so` workflow（它的 `paths` 过滤只监听 `hachimi_ura_plugin/**`），
不参与编译，可随时增删。

## 2026-11-27 · ci_patch11（worker 活跃门禁 + chara 类校验）

### 三次崩溃，各归各的

| 崩溃 | 原因 | 状态 |
|---|---|---|
| 开自动后闪退（`matsuri_data` 刚落盘） | `Data::init` 解析 7.3 MB 跑在渲染线程 | 已搬到 worker |
| `socket hang up` | HTTP 线程调静态 `ActListRepository.Get()` | 不再尝试 |
| **反复闪退（3 分钟一崩）** | worker 只等 5 秒就开跑，实际还在标题画面，`get_Character` 拿到不可用对象；每 3 秒重试 | **本补丁修** |

### 两道门禁

```
1. worker 活跃门
   hook 命中 GetBaseWeight → 写 MT_LAST_HIT 时间戳
   worker 仅当最近 10 秒内有命中才动游戏内存
   标题画面 / 结算界面 hook 不命中 → worker 不跑

2. chara 类指针硬校验
   读任何 Character 字段前先确认 klass 名是 WorkSingleModeCharaData
   挡住"非空但已释放/被复用"的对象（§6.2 同源问题）
```

### 安全版本（已回退并确认可进游戏）

```
libhachimi_ura-3.29.1-da44ecc5.so   14,361,416
  无 matsuri 闭环、无 worker → 只会观测，不会碰游戏对象
  /api/ai/deck not_found 可作为识别特征
```

## 已确认的映射（游戏 2.33.2 / setting 1501）

```
150101 → 速度    Train(0)      150104 → 根性    Train(3) 排除法
150102 → 耐力    Train(1)      150105 → 智力    Train(4)
150103 → 力量    Train(2)
150120..150124 → ActTrain(0..4)  演目下标，与 acts[].train 一致
```

## 支援卡读法（已实测）

```
元素是引用（seq_item 取指针）；三字段都是 ObscuredInt、间距 20 字节：
  +16 position   +36 supportCardId   +56 limitBreak
卡号 = supportCardId * 10 + limitBreak
实测：303224 303114 302754 302894 303044 303232（uma 100801）
```

## 待补的真实数据

2.33.2 里 `WorkSingleModeScenarioFesta +16 DataSet` 运行时为 null，
数据搬到 `SingleMode.ScenarioFesta.*`：

```
ActEntity  +16 ActType  +24 ActMemberCharaIdList  +32 MaxActTrainingLevel
           +36 ActTrainingState  +56 CurrentActGauge  +60 LevelUpByFriendOuting
ActListEntity  +16 ActEntityList   +24 ActTrainingStep
```

目前用空 acts 顶（matsuri 合法缺省），稽古估值偏低，**补齐前不能拿稽古决策当真**。