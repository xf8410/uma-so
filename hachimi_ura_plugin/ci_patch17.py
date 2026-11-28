#!/usr/bin/env python3
"""CI 侧补丁 17：根除训练/稽古排序清零 + 接入 5 个非训练指令（150106..150110）15 槽全统筹。

解决的问题（基于 ci_patch16 + ARM64 反汇编 & 单变量实测验证）：
  1. 扩充 MT_W 从 10 槽到 15 槽，并扩展 mt_slot() 与 /api/ai/matsuri 的 weights[]：
       0..4   速 / 耐 / 力 / 根 / 智          (150101..150105)
       5..9   稽1..稽5                        (150120..150124)
       10     休（休息 / 合宿休息）            (150106)
       11     友（友人 / 团队出行）            (150107)
       12     外（育成马娘普通外出）            (150108)
       13     保（保健室）                     (150109)
       14     赛（比赛）                       (150110)

  2. 彻底根除 ci_patch15/16 中“首选非训练时把训练/稽古排序全清零”的缺陷：
     - next_w 默认初始化为 [-1; 15]（-1 = 不接管退回原值，绝不默认清 0）；
     - 只要 decision.actions 中包含有效训练/稽古打分，无论 chosen_tag 是不是训练/稽古，
       都一律按 (score - min_score) / span 写入保序权重：
       * 首选落在训练/稽古 (0..10)：
           - unified_10slot: 候选映到 10..200，首选 1000；非训练槽 10..15 保持 -1（不干扰必跑赛 +1200）
           - 降级态: 稽古 5..10 保持 -1，训练映到 10..70、首选 170；非训练槽保持 -1
       * 首选落在非训练 (10..15：休/友/外/保/赛)：
           - 训练/稽古候选保序压低到 1..40（降级态训练 1..30、稽古 -1），绝不清零抹平排序！
           - 同时将 chosen_tag 对应的非训练槽（10..14）直接拉到 1000，确保压过训练的 1..40 + ≤242 条件加分；
             万一遇到游戏硬过滤（如体力>=100、友人未解锁、合宿不可外出）被迫回退训练时，
             仍完整按 matsuri 的 1..40 排序选最优训练/稽古！
       * 无有效训练/稽古打分（如纯教诲帧 4000）：全部保持 -1 放行原版。
"""

import sys

PATH = "src/lib.rs"

MT_W_OLD = r'''/// Train(0..4) / ActTrain(0..4) 的归一化权重（0..200）
static MT_W: [std::sync::atomic::AtomicI32; 10] = [
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
];

/// tag → MT_W 下标
fn mt_slot(tag: i32) -> Option<usize> {
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

MT_W_NEW = r'''/// ★ ci_patch17: 15 槽归一化权重（0..4 训、5..9 稽、10 休、11 友、12 外、13 保、14 赛；-1=不接管）
static MT_W: [std::sync::atomic::AtomicI32; 15] = [
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
    std::sync::atomic::AtomicI32::new(-1),
];

/// tag → MT_W 下标（0..15 全覆盖 setting=1501 全部指令）
fn mt_slot(tag: i32) -> Option<usize> {
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

WEIGHT_FILL_OLD = r'''    let mut min_score = f64::INFINITY;
    let mut max_score = f64::NEG_INFINITY;
    for (tag, s) in decision.actions.iter() {
        if let Some(slot) = mt_slot(*tag) {
            if (unified_10slot || slot < 5) && s.is_finite() {
                if *s < min_score {
                    min_score = *s;
                }
                if *s > max_score {
                    max_score = *s;
                }
            }
        }
    }
    let span = max_score - min_score;

    let mut next_w = [0i32; 10];
    if !unified_10slot {
        // 仅在 dataset_missing 降级态下保留原版稽古 200
        for i in 5..10 {
            next_w[i] = -1;
        }
        if let Some(best_slot) = mt_slot(decision.chosen_tag) {
            if best_slot < 5 {
                for (tag, score) in decision.actions.iter() {
                    if let Some(slot) = mt_slot(*tag) {
                        if slot < 5 {
                            let w = if score.is_finite() && span > 1e-6 {
                                (10.0 + 60.0 * ((*score - min_score) / span)).round() as i32
                            } else {
                                40
                            };
                            next_w[slot] = w.clamp(10, 70);
                        }
                    }
                }
                next_w[best_slot] = 170;
            }
        }
    } else if let Some(best_slot) = mt_slot(decision.chosen_tag) {
        // ★ 真实 Festa 10 槽同标尺统筹：首选 1000 彻底压死 +242 原版条件加成噪声，次选按相对分映到 10..200
        for (tag, score) in decision.actions.iter() {
            if let Some(slot) = mt_slot(*tag) {
                let w = if score.is_finite() && span > 1e-6 {
                    (10.0 + 190.0 * ((*score - min_score) / span)).round() as i32
                } else {
                    100
                };
                next_w[slot] = w.clamp(10, 200);
            }
        }
        next_w[best_slot] = 1000;
    }
    // 若首选是休息/外出/保健/比赛，unified_10slot 下 next_w[0..10] 全为 0，放行原版休息/外出/保健/比赛'''

WEIGHT_FILL_NEW = r'''    // ★ ci_patch17: 仅统计训练/稽古槽（slot < active_limit）的分值区间，绝不把非训练槽混入训练归一化
    let active_limit: usize = if unified_10slot { 10 } else { 5 };
    let mut min_score = f64::INFINITY;
    let mut max_score = f64::NEG_INFINITY;
    for (tag, s) in decision.actions.iter() {
        if let Some(slot) = mt_slot(*tag) {
            if slot < active_limit && s.is_finite() {
                if *s < min_score {
                    min_score = *s;
                }
                if *s > max_score {
                    max_score = *s;
                }
            }
        }
    }
    let has_train_scores = min_score.is_finite() && max_score.is_finite();
    let span = if has_train_scores {
        max_score - min_score
    } else {
        0.0
    };

    // 默认全部 -1（不接管退回原值），绝不默认清零！
    let mut next_w = [-1i32; 15];
    let best_slot = mt_slot(decision.chosen_tag);
    let best_is_train = matches!(best_slot, Some(s) if s < active_limit);
    let best_is_non_train = matches!(best_slot, Some(10..=14));

    if has_train_scores {
        // 先将 active_limit 内未出现在候选中的不可用槽置 0，再把全部有分候选按相对分保序写入
        for i in 0..active_limit {
            next_w[i] = 0;
        }
        let (lo, hi, mid): (f64, f64, i32) = if best_is_non_train {
            // ★ 首选为非训练（休/友/外/保/赛）：压低训练量级但完整保留相对排序，绝不清零！
            if unified_10slot {
                (1.0, 40.0, 20)
            } else {
                (1.0, 30.0, 15)
            }
        } else if unified_10slot {
            (10.0, 200.0, 100)
        } else {
            (10.0, 70.0, 40)
        };
        let lo_i = lo as i32;
        let hi_i = hi as i32;
        for (tag, score) in decision.actions.iter() {
            if let Some(slot) = mt_slot(*tag) {
                if slot < active_limit {
                    let w = if score.is_finite() && span > 1e-6 {
                        (lo + (hi - lo) * ((*score - min_score) / span)).round() as i32
                    } else {
                        mid
                    };
                    next_w[slot] = w.clamp(lo_i, hi_i);
                }
            }
        }
    }

    // 拉高最终首选槽位：
    // - 训练/稽古首选：unified 态给 1000（降级态给 170 低于原版 200 稽古），非训练槽 10..15 保持 -1
    // - 非训练首选（10 休 / 11 友 / 12 外 / 13 保 / 14 赛）：给 1000 压过训练的 1..40 + ≤242 条件加分；
    //   若被游戏硬过滤（如体力>=100），游戏回退选训练时仍按 1..40 保序选最优项！
    if let Some(b) = best_slot {
        if best_is_train {
            next_w[b] = if unified_10slot { 1000 } else { 170 };
        } else if best_is_non_train {
            next_w[b] = 1000;
        }
    }'''

MT_JSON_OLD = r'''    let names = ["速", "耐", "力", "根", "智", "稽1", "稽2", "稽3", "稽4", "稽5"];'''
MT_JSON_NEW = r'''    let names = [
        "速", "耐", "力", "根", "智", "稽1", "稽2", "稽3", "稽4", "稽5", "休", "友", "外", "保",
        "赛",
    ];'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch17: {msg}")
    return 1


def _replace_once(s: str, old: str, new: str, label: str) -> str:
    n = s.count(old)
    if n != 1:
        raise RuntimeError(f"anchor '{label}' count={n}")
    return s.replace(old, new, 1)


def main() -> int:
    s = open(PATH, encoding="utf-8").read()
    before = len(s)
    try:
        s = _replace_once(s, MT_W_OLD, MT_W_NEW, "mt_w_and_slot_15")
        s = _replace_once(s, WEIGHT_FILL_OLD, WEIGHT_FILL_NEW, "weight_fill_preserve_order")
        s = _replace_once(s, MT_JSON_OLD, MT_JSON_NEW, "mt_json_15_names")
    except RuntimeError as e:
        return _fail(str(e))

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    for needle in (
        "static MT_W: [std::sync::atomic::AtomicI32; 15]",
        "TAG_FRIEND_OUTING",
        "let mut next_w = [-1i32; 15];",
        "best_is_non_train",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())
