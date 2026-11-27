# 构建触发记录

本文件只用来触发 `build-so` workflow（它的 `paths` 过滤只监听 `hachimi_ura_plugin/**`），
不参与编译，可随时增删。

## 2026-11-27 · ci_patch8（支援卡按 ObscuredInt 读）

`/api/ai/deckraw` 的原始字节把布局定死了：

```
元素 = 引用（seq_item 取到有效 ptr），不是值类型内联
对象内三个字段全是 ObscuredInt，间距 20 字节：
  +16 position      key 444444, hidden 444445 → 1
  +36 supportCardId key 444444, hidden 442990 → 30322
  +56 limitBreak    key 444444, hidden 444440 → 4      → 卡号 303224
六个元素 position 依次解出 1..6，解释成立
```

插件里 `IL2CPP_SUPPORT_CARD_ID_OFF=0x14 / LIMIT_OFF=0x18` 那组是紧凑布局常量，
对这组字段不适用（用它会读到 key/hidden 的低位，也就是之前 `card:117` 那种垃圾）。

## 状态说明（重要）

`/api/ai/matsuri` 的 `state` 一直是 0，因为 `cards` 之前恒为空 → matsuri 卡在编组门禁 E4。
所以到目前为止，游戏里看到的所有选择（速度、休息、根性、稽古）**都是原版 omakase 在决定**，
hook 只是把原值透传。这一点不能混。

稽古被选中是原版行为：稽古位权重 200，训练位才 10。

## 已确认的映射（游戏 2.33.2 / setting 1501）

```
150101 → 速度    Train(0)
150102 → 耐力    Train(1)
150103 → 力量    Train(2)
150104 → 根性    Train(3)   排除法
150105 → 智力    Train(4)
150120..150124 → ActTrain(0..4)  演目下标，与 acts[].train 一致
```

## 装 so 之后的核对顺序

```
1. /api/ai/deck        uma + cards[6] 是否就是本局的马娘与六张卡
2. /api/ai/matsuri     state 从 0 变 1，weights[] 出现非 0
3. /api/ai/weightcalls hits 涨，ret 不再是 10/200 这种原版值
4. 观察训练选择是否跟着 matsuri 的 weights 走
```