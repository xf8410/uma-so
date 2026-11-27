# 构建触发记录

本文件只用来触发 `build-so` workflow（它的 `paths` 过滤只监听 `hachimi_ura_plugin/**`），
不参与编译，可随时增删。

## 2026-11-27 · ci_patch9（matsuri 搬到后台线程）

### 闪退现场

- `matsuri_data/` 目录在闪退那一刻创建，6 个文件 7.3 MB 全部落盘（含 5.7 MB text_data_dict.json）
- `ura.log` 无崩溃记录，`/data/tombstones` 最新 11-18、`/data/anr` 最新 11-23，都不是这次
- 插件自己装了 SIGSEGV handler，段错误被它接住后直接退出，所以系统层不留 tombstone

结论：**崩在 `Data::init` 解析那 7.3 MB**，而它当时跑在 `GetBaseWeight` 的 hook 里
（游戏渲染线程）。不是偏移问题，是线程放错。

### 改法

```
hook GetBaseWeight     只读 MT_W 静态缓存，命中即返回，零计算
matsuri-worker 线程    启动等 5 秒 → 每 3 秒 run 一次 identity→init→frame→decide
                       → 写 MT_W；每轮 catch_unwind；栈 8 MB
```

未就绪时 hook 原样透传，行为等同没装闭环。

### 判断闭环是否真的接管

```
/api/ai/matsuri   state 0→1，weights[] 出现 10..200 的分
/api/ai/weightcalls  hits 涨，ret 不再是原版 10/200
```

`state` 一直是 0 时，游戏里看到的一切选择都是**原版 omakase** 决定的，不能算接管。

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
元素是引用（seq_item 取指针），三个字段都是 ObscuredInt、间距 20 字节：
  +16 position   +36 supportCardId   +56 limitBreak
卡号 = supportCardId * 10 + limitBreak
```