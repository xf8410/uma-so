# 构建触发记录

本文件只用来触发 `build-so` workflow（它的 `paths` 过滤只监听 `hachimi_ura_plugin/**`），
不参与编译，可随时增删。

## 2026-11-27 · ci_patch7（支援卡数组原始字节）

ci_patch6 的值类型读法出错：`pos=369433728` 明显是指针而不是 0..5。
而 `/debug/training_partners` 自相矛盾：`element_is_value_type=false` 却 `element_size=128`，
按引用取元素又全是 `ptr: null`。

这版不再猜，只导出证据：

```
/api/ai/deckraw
  chara / arr 地址、+0x18 长度
  head_hex        数组对象头 0x40
  body_hex_320    +0x20 起 320 字节
  as_value_inline128  按"值类型内联 128"解释的前 6 组 (pos,card,lb)
  as_ref_ptr          按"8 字节引用指针"解释的前 6 组
  obj0_hex64          第一个非空引用对象的前 64 字节
```

## ci_patch6 的遗留问题

- `mt_read_identity()` 目前读出来是垃圾，`cards` 恒为空 → matsuri 卡在编组门禁
- `/api/ai/matsuri` 的 `state` 因此一直是 0，hook 仍返回原版权重
- 也就是说：这段时间看到的"速度一次、休息一次"都是原版 omakase 在决定

## 已确认的映射（游戏 2.33.2 / setting 1501）

```
150101 → 速度    Train(0)
150102 → 耐力    Train(1)
150103 → 力量    Train(2)
150104 → 根性    Train(3)   排除法
150105 → 智力    Train(4)
150120..150124 → ActTrain(0..4)  演目下标，与 acts[].train 一致
```