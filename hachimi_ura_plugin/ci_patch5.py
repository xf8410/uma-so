#!/usr/bin/env python3
"""CI 侧补丁 5：GetBaseWeight 直连 matsuri 决策（内部闭环）。

目标形态（不再依赖任何外部文件）：
    hook GetBaseWeight(this, commandIdType)
      ├─ 手工覆盖表命中        → 直接返回（调试用，优先级最高）
      ├─ commandIdType 是训练位/稽古位
      │    ├─ 距上次计算 >3s → build_matsuri_frame() 拼帧
      │    │                    matsuri_bridge::decide() 算分
      │    │                    按 (tag → 归一化权重) 写静态表
      │    └─ 查静态表命中     → 返回 matsuri 给这一位的权重
      └─ 其余                  → trampoline 原值

omakase 编号（实测锁定，游戏 2.33.2 / setting 1501）：
    150101..150105 = Train(0..4)      速 耐 力 根 智
    150120..150124 = ActTrain(0..4)   演目下标
    与 matsuri 的 training.normal[] / acts[].train 逐位一致，无需转换表。

尺度换算：
    原版权重训练位 10、稽古位 200，量纲差 20 倍；matsuri 的分数是同一量纲
    （value.rs：全部估值都是评分点）。所以统一归一化到 [10, 200]：
        权重 = 10 + 190 × (score / max_score)
    这样 matsuri 认为该训练优于稽古时，训练能拿到 200 压过稽古。
    不在候选里的位（matsuri 没给）置 0，等于不可选。

安全：
    - 帧构造与决策都包在 catch_unwind 里，任何失败都退回原值，不连着游戏一起崩；
    - 3 秒节流：decide 耗时 100~800ms，不能每次 hook 都跑；
    - 失败原因单独记，端点可查，不静默。
"""

import sys

PATH = "src/lib.rs"

CLOSED_LOOP = r'''
// ===== matsuri 决策直连（GetBaseWeight 闭环）=====
//
// tag 索引：Train(0..4) → 0..4，ActTrain(0..4) → 5..9
static MT_STATE: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);
// 0 = 未就绪 / 1 = 就绪 / 2 = 上次失败
static MT_AT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static MT_HITS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static MT_TURN: std::sync::atomic::AtomicI32 = std::sync::atomic::AtomicI32::new(-1);
static MT_CHOSEN_TAG: std::sync::atomic::AtomicI32 = std::sync::atomic::AtomicI32::new(-1);
static MT_ERR: std::sync::Mutex<String> = std::sync::Mutex::new(String::new());

/// Train(0..4) / ActTrain(0..4) 的归一化权重（0..200）
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
}

fn mt_set_err(msg: String) {
    if let Ok(mut g) = MT_ERR.lock() {
        *g = msg;
    }
}

/// 拼帧 + 决策 + 归一化写表；失败只记录原因，清 MT_STATE
unsafe fn mt_recompute() {
    use std::sync::atomic::Ordering::Relaxed;

    if !matsuri_bridge::ready() {
        // 首次：用默认身份初始化（uma/cards 取内嵌 ai_config，seed 固定）
        if let Err(e) = matsuri_bridge::init(0, &[], 61444) {
            MT_STATE.store(2, Relaxed);
            mt_set_err(format!("init: {e}"));
            return;
        }
    }

    let frame = build_matsuri_frame();
    if frame.contains("\"error\"") {
        MT_STATE.store(2, Relaxed);
        mt_set_err(format!("frame: {}", frame.chars().take(120).collect::<String>()));
        return;
    }

    let decision = match matsuri_bridge::decide(&frame) {
        Ok(d) => d,
        Err(e) => {
            MT_STATE.store(2, Relaxed);
            mt_set_err(format!("decide: {e}"));
            return;
        }
    };

    // 找最大分做归一化基准
    let mut max_score = f64::NEG_INFINITY;
    for (_, s) in decision.actions.iter() {
        if s.is_finite() && *s > max_score {
            max_score = *s;
        }
    }

    // 先全部置 0（不可选），再把 matsuri 给出的候选按比例写进去
    for i in 0..MT_W.len() {
        MT_W[i].store(0, Relaxed);
    }
    for (tag, score) in decision.actions.iter() {
        if let Some(slot) = mt_slot(*tag) {
            let w = if max_score.is_finite() && max_score > 0.0 && score.is_finite() {
                (10.0 + 190.0 * (score / max_score)).round() as i32
            } else {
                10
            };
            MT_W[slot].store(w.clamp(0, 200), Relaxed);
        }
    }

    MT_TURN.store(decision.turn, Relaxed);
    MT_CHOSEN_TAG.store(decision.chosen_tag, Relaxed);
    MT_STATE.store(1, Relaxed);
    mt_set_err(String::new());
}

/// hook 用：给定 omakase 行号，返回 matsuri 的权重（None = 不接管）
unsafe fn mt_weight_for_arg(arg: i32) -> Option<i32> {
    use std::sync::atomic::Ordering::Relaxed;

    // 节流：3 秒最多算一次（decide 耗时 100~800ms，不能每次 hook 都跑）
    let now = wb_now_ms();
    let last = MT_AT.load(Relaxed);
    if last == 0 || now.saturating_sub(last) >= 3000 {
        MT_AT.store(now, Relaxed);
        mt_recompute();
    }

    if MT_STATE.load(Relaxed) != 1 {
        return None;
    }
    let tag = matsuri_bridge::omakase_arg_to_tag(arg)?;
    let slot = mt_slot(tag)?;
    let w = MT_W[slot].load(Relaxed);
    if w < 0 {
        None
    } else {
        Some(w)
    }
}

fn mt_json() -> String {
    use std::sync::atomic::Ordering::Relaxed;
    let names = ["速", "耐", "力", "根", "智", "稽1", "稽2", "稽3", "稽4", "稽5"];
    let mut parts: Vec<String> = Vec::new();
    for i in 0..MT_W.len() {
        parts.push(format!(
            r#"{{"slot":"{}","w":{}}}"#,
            names[i],
            MT_W[i].load(Relaxed)
        ));
    }
    let err = MT_ERR
        .lock()
        .map(|g| g.clone())
        .unwrap_or_default()
        .replace('"', "'");
    format!(
        r#"{{"ok":true,"state":{},"ready":{},"turn":{},"chosen_tag":{},"hits":{},"weights":[{}],"last_error":"{}"}}"#,
        MT_STATE.load(Relaxed),
        matsuri_bridge::ready(),
        MT_TURN.load(Relaxed),
        MT_CHOSEN_TAG.load(Relaxed),
        MT_HITS.load(Relaxed),
        parts.join(","),
        err
    )
}
'''

HOOK_OLD = """        WB_LOG[slot].store(arg0 as i64, Relaxed);
        WB_LOG[slot + 1].store(orig as i64, Relaxed);
        orig"""

HOOK_NEW = """        // matsuri 闭环：训练位 / 稽古位按 matsuri 的分数返回
        if let Some(v) = mt_weight_for_arg(arg0) {
            MT_HITS.fetch_add(1, Relaxed);
            WB_LOG[slot].store(arg0 as i64, Relaxed);
            WB_LOG[slot + 1].store(v as i64, Relaxed);
            return v;
        }
        WB_LOG[slot].store(arg0 as i64, Relaxed);
        WB_LOG[slot + 1].store(orig as i64, Relaxed);
        orig"""

ROUTE_OLD = '    } else if path == "/api/ai/init" {'
ROUTE_NEW = '''    } else if path == "/api/ai/matsuri" {
        mt_json()
    } else if path == "/api/ai/init" {'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch5: {msg}")
    return 1


def main() -> int:
    s = open(PATH, encoding="utf-8").read()
    before = len(s)

    anchor = "unsafe fn install_weight_hook() -> String {"
    if s.count(anchor) != 1:
        return _fail(f"anchor install_weight_hook count={s.count(anchor)}")
    s = s.replace(anchor, CLOSED_LOOP.strip() + "\n\n" + anchor, 1)

    if s.count(HOOK_OLD) != 1:
        return _fail(f"hook tail anchor count={s.count(HOOK_OLD)}")
    s = s.replace(HOOK_OLD, HOOK_NEW, 1)

    anchor = '    "/api/ai/entry",'
    if s.count(anchor) != 1:
        return _fail(f"anchor ENDPOINTS entry count={s.count(anchor)}")
    s = s.replace(anchor, anchor + '\n    "/api/ai/matsuri",', 1)

    if s.count(ROUTE_OLD) != 1:
        return _fail(f"route anchor init count={s.count(ROUTE_OLD)}")
    s = s.replace(ROUTE_OLD, ROUTE_NEW, 1)

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    for needle in (
        "fn mt_recompute",
        "fn mt_weight_for_arg",
        "fn mt_json",
        "mt_weight_for_arg(arg0)",
        "/api/ai/matsuri",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())