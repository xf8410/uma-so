#!/usr/bin/env python3
"""CI 侧补丁 12c：接管 omakase 非训练指令 150106–150110（QQ 侧新实测全表）。

依据：`/sdcard/Download/QQ/OMAKASE_NON_TRAINING_ENTRIES.md`（2026-11-28，ARM64 反汇编 +
master.mdb 双验，5 项里 4 项已实测锁定）：

    150106 = 休息 / 合宿休息（GetHoliday / GetSummerCamp，共用同号）  基础 10   → tag 3001
    150107 = 友人・团队出行（GetOuting）                              基础 20   → tag 3003
    150108 = 育成马娘普通外出（GetOutingTrainingChara）               基础  0   → tag 3002
    150109 = 保健室（GetHospital）                                    基础  0   → tag 3004
    150110 = 比赛（GetRace）                                          基础 10   → tag 3000

之前 `MT_W` 只有 10 个槽（训练 0..5 + 稽古 5..10），`mt_weight_for_arg` 只认
150101..150105 / 150120..150124，所以 matsuri 就算算出「该休息 / 该比赛」，
也没法把意图传给游戏 —— 这是故障 #11「AI 被架空」的另一半根因。

本补丁：
  1. `MT_W` 扩到 15 槽：0..5 训练、5..10 稽古、10..15 非训练
     （10 休息 / 11 友人出行 / 12 普通外出 / 13 保健室 / 14 比赛）；
  2. 新增 `mt_arg_to_tag()`，把 150106..150110 映射到对应的 `matsuri_bridge::TAG_*`；
     `mt_weight_for_arg` 改用它（不再只依赖 bridge 里那两段）；
  3. `mt_recompute` 里：稽古槽循环收窄为 `5..10`，新增非训练槽 `10..15`：
     本轮 matsuri 没给出该动作 → 保持 -1（不接管，原版权重 + 条件加分继续生效）；
  4. `/api/ai/matsuri` 的槽位名字补上「休 / 友出 / 外出 / 保健 / 比赛」，便于现场核对。

注意（写进注释，避免误解）：
  非训练位的最终权重 = 我们的值 + 原版 `omakase_base_pt` 条件加分，
  比赛最高 +1200、休息 +60/+90 —— 我们给的是同尺度 0..200，
  所以这里只能「改变倾向」，不能强行压过必跑赛（这符合原版语义）。
"""

import sys

PATH = "src/lib.rs"

# 0..5 训练 / 5..10 稽古 / 10..15 非训练
MT_W_OLD = (
    "static MT_W: [std::sync::atomic::AtomicI32; 10] = [\n"
    + "    std::sync::atomic::AtomicI32::new(-1),\n" * 10
    + "];"
)

MT_W_NEW = (
    "// ★ v3.30.3：槽位扩到 15 个 —— 0..5 训练、5..10 稽古、10..15 非训练\n"
    "// （10 休息 / 11 友人出行 / 12 普通外出 / 13 保健室 / 14 比赛）。\n"
    "// 编号归属见 QQ/OMAKASE_NON_TRAINING_ENTRIES.md（ARM64 汇编 + master.mdb 双验）。\n"
    "static MT_W: [std::sync::atomic::AtomicI32; 15] = [\n"
    + "    std::sync::atomic::AtomicI32::new(-1),\n" * 15
    + "];"
)

SLOT_OLD = r'''fn mt_slot(tag: i32) -> Option<usize> {
    if (matsuri_bridge::TAG_TRAIN_BASE..matsuri_bridge::TAG_TRAIN_BASE + 5).contains(&tag) {
        Some((tag - matsuri_bridge::TAG_TRAIN_BASE) as usize)
    } else if (matsuri_bridge::TAG_ACTTRAIN_BASE..matsuri_bridge::TAG_ACTTRAIN_BASE + 5)
        .contains(&tag)
    {
        Some(5 + (tag - matsuri_bridge::TAG_ACTTRAIN_BASE) as usize)
    } else {
        None
    }
}'''

SLOT_NEW = r'''fn mt_slot(tag: i32) -> Option<usize> {
    if (matsuri_bridge::TAG_TRAIN_BASE..matsuri_bridge::TAG_TRAIN_BASE + 5).contains(&tag) {
        Some((tag - matsuri_bridge::TAG_TRAIN_BASE) as usize)
    } else if (matsuri_bridge::TAG_ACTTRAIN_BASE..matsuri_bridge::TAG_ACTTRAIN_BASE + 5)
        .contains(&tag)
    {
        Some(5 + (tag - matsuri_bridge::TAG_ACTTRAIN_BASE) as usize)
    } else if tag == matsuri_bridge::TAG_REST {
        Some(10)
    } else if tag == matsuri_bridge::TAG_FRIEND_OUTING {
        Some(11)
    } else if tag == matsuri_bridge::TAG_NORMAL_OUTING {
        Some(12)
    } else if tag == matsuri_bridge::TAG_CLINIC {
        Some(13)
    } else if tag == matsuri_bridge::TAG_RACE {
        Some(14)
    } else {
        None
    }
}'''

ARG_FN = r'''/// ★ v3.30.3：omakase 行号 → 动作标记。
///
/// 除了训练（150101..150105）与稽古（150120..150124），补上 5 个非训练指令。
/// 归属来自 ARM64 反汇编（6 个 Get*BaseWeight 里的立即数偏移）+ master.mdb 双验：
///   150106 休息 / 合宿休息（两个方法共用同号）
///   150107 友人・团队出行
///   150108 育成马娘普通外出
///   150109 保健室
///   150110 比赛
fn mt_arg_to_tag(arg: i32) -> Option<i32> {
    match arg {
        150101..=150105 => Some(matsuri_bridge::TAG_TRAIN_BASE + (arg - 150101)),
        150120..=150124 => Some(matsuri_bridge::TAG_ACTTRAIN_BASE + (arg - 150120)),
        150106 => Some(matsuri_bridge::TAG_REST),
        150107 => Some(matsuri_bridge::TAG_FRIEND_OUTING),
        150108 => Some(matsuri_bridge::TAG_NORMAL_OUTING),
        150109 => Some(matsuri_bridge::TAG_CLINIC),
        150110 => Some(matsuri_bridge::TAG_RACE),
        _ => None,
    }
}

'''

ACT_OLD = r'''    // 稽古槽 5..10：只有本轮真的给出稽古候选才接管，否则保持 -1 让原值生效
    for i in 5..MT_W.len() {
        MT_W[i].store(if has_act_candidates { 0 } else { -1 }, Relaxed);
    }'''

ACT_NEW = r'''    // 稽古槽 5..10：只有本轮真的给出稽古候选才接管，否则保持 -1 让原值生效
    for i in 5..10 {
        MT_W[i].store(if has_act_candidates { 0 } else { -1 }, Relaxed);
    }
    // ★ v3.30.3：非训练槽 10..15 —— 默认全部 -1（不接管），
    // 只有本轮 matsuri 真的给出对应动作时，下面按 actions 写值才会覆盖它。
    // -1 表示「返回 trampoline 原值」：休息 10 + 体力条件 +60/+90、
    // 友人出行 20、普通外出 0、保健室 0（病种 +100/种）、比赛 10（必跑赛 +1200）都会照常生效。
    for i in 10..15 {
        MT_W[i].store(-1, Relaxed);
    }'''

ARG_OLD = "    let tag = matsuri_bridge::omakase_arg_to_tag(arg)?;"
ARG_NEW = "    let tag = mt_arg_to_tag(arg)?;"

NAMES_OLD = r'''    let names = ["速", "耐", "力", "根", "智", "稽1", "稽2", "稽3", "稽4", "稽5"];'''
NAMES_NEW = r'''    let names = [
        "速", "耐", "力", "根", "智", "稽1", "稽2", "稽3", "稽4", "稽5",
        "休", "友出", "外出", "保健", "比赛",
    ];'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch12c: {msg}")
    return 1


def main() -> int:
    s = open(PATH, encoding="utf-8").read()
    before = len(s)

    if s.count(MT_W_OLD) != 1:
        return _fail(f"MT_W block anchor count={s.count(MT_W_OLD)}")
    s = s.replace(MT_W_OLD, MT_W_NEW, 1)

    anchor = "fn mt_slot(tag: i32) -> Option<usize> {"
    if s.count(anchor) != 1:
        return _fail(f"mt_slot anchor count={s.count(anchor)}")
    s = s.replace(anchor, ARG_FN + anchor, 1)

    if s.count(SLOT_OLD) != 1:
        return _fail(f"mt_slot body anchor count={s.count(SLOT_OLD)}")
    s = s.replace(SLOT_OLD, SLOT_NEW, 1)

    if s.count(ACT_OLD) != 1:
        return _fail(f"act slot anchor count={s.count(ACT_OLD)}")
    s = s.replace(ACT_OLD, ACT_NEW, 1)

    if s.count(ARG_OLD) != 1:
        return _fail(f"arg->tag anchor count={s.count(ARG_OLD)}")
    s = s.replace(ARG_OLD, ARG_NEW, 1)

    if s.count(NAMES_OLD) != 1:
        return _fail(f"names anchor count={s.count(NAMES_OLD)}")
    s = s.replace(NAMES_OLD, NAMES_NEW, 1)

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")

    for needle in (
        "fn mt_arg_to_tag",
        "AtomicI32; 15",
        "150106 => Some(matsuri_bridge::TAG_REST)",
        "for i in 10..15 {",
        "mt_arg_to_tag(arg)?",
        '"比赛"',
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits}")
        if hits == 0:
            return _fail(f"补丁后仍缺 {needle}")

    print("ci_patch12c: all anchors ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())