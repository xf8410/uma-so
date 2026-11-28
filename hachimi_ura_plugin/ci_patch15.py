#!/usr/bin/env python3
"""CI 侧补丁 15：保护骏大祭稽古权重 + 重复帧不清零 + 补读训练设施等级。

实测根因（2026-11-28，雷电 14 + 5d9d58ba，PID 7548 实跑至 Turn 63）：
  1. matsuri 决策与权重替换已跑通（decided=146, failed=0, matsuri.hits=180, 全程零闪退），
     但因 2.33.2 演目 acts 仍为空缺省（dataset_missing），matsuri 候选不含 ActTrain，
     ci_patch14 将 next_w[5..10]（150120..150124 稽古）全写成了 0，直接压掉了原版 200
     的骏大祭稽古权重，导致本局几乎不点稽古、Turn 63 五维偏低（1614/792/1047/745/946）。
     → 当候选不含 ActTrain 时：
       * 稽古槽 next_w[5..10] 写 -1（mt_weight_for_arg 返回 None → 完整保留原版 200 稽古）；
       * 普通训练槽 next_w[0..5]：首选给 170（低于原版稽古 200，不抢稽古；但比次选 10..70
         高出 100~160 分，在无稽古回合稳稳执行 matsuri 选出的普通训练）；
       * 当 matsuri 选休息/外出/保健/比赛时，普通训练槽写 0，稽古槽仍写 -1。
  2. 同一回合 3 秒后重复帧触发 matsuri G7 幂等 `skip: 重复帧`，ci_patch14 将 MT_STATE
     重置为 0，导致约 1/3 窗口短暂退回原版。
     → 遇到 `重复帧` 时直接 return，保留本回合已算好的 MT_STATE=1 与 MT_W。
  3. build_matsuri_frame() 里 trainLevelCount 恒为 [0,0,0,0,0]、trainLevelTotal 为 null。
     → 从 CharaData+736 (<TrainingLevelInfoArray>) 安全读取 5 项训练当前等级 (Lv1..5)，
       填入 baseGame.trainLevelCount ([(lv-1)*4]) 与 festa.trainLevelTotal ([lv])。
"""

import sys

PATH = "src/lib.rs"

FRAME_TL_OLD = r'''    let is_ill_real = eff_ids.iter().any(|&x| (1..=6).contains(&x));

    if dataset_obj.is_null() {
        // 2.33.2 里 FestaDataSet 运行时不再绑定，输出完整 baseGame + 空 acts 缺省。
        return format!(
            r#"{{"festa":{{"acts":[],"maxActLevel":10,"campRemain":0,"trainLevelTotal":null,"persons":[],"checkPoints":[]}},"baseGame":{{"scenarioId":{},"umaId":{},"turn":{},"vital":{},"maxVital":{},"motivation":{},"fiveStatus":[{},{},{},{},{}],"fiveStatusLimit":[{},{},{},{},{}],"skillPt":{},"skillScore":0,"totalHints":0,"trainLevelCount":[0,0,0,0,0],"ptScoreRate":1.0,"failureRateBias":0,"isIll":{},"isQieZhe":false,"isXiaoQie":false,"isAiJiao":false,"PositiveThinkingCount":0,"isRefreshMind":false,"LuckyCount":0,"zhongMaBlueCount":[0,0,0,0,0],"isRacing":false,"cardId":[{}],"persons":[{}],"personDistribution":[{}],"lockedTrainingId":-1,"friendship_noncard_yayoi":{},"friendship_noncard_reporter":{},"friend_stage":0,"friend_outgoingUsed":0,"playing_state":1,"source":"command","isAfterTraining":false,"charaEffects":[{}],"training":{{}}}},"notes":["dataset_missing"]}}"#,
            sid,
            uma_id,
            turn,
            vital,
            max_vital,
            motivation,
            f[0], f[1], f[2], f[3], f[4],
            lim[0], lim[1], lim[2], lim[3], lim[4],
            skill_pt,
            if is_ill_real { "true" } else { "false" },
            card_id_json,
            persons_json,
            dist_json,
            yayoi_bond,
            reporter_bond,
            eff_json
        );
    }'''

FRAME_TL_NEW = r'''    let is_ill_real = eff_ids.iter().any(|&x| (1..=6).contains(&x));

    // ★ ci_patch15: 读取 5 项训练设施等级（CharaData+736 <TrainingLevelInfoArray>）
    let mut train_lv = [1i32; 5];
    let mut got_train_lv = false;
    let mut tl_arr = read_ptr_at(chara, 736);
    if !is_valid_user_ptr(tl_arr) && !chara_class.is_null() {
        tl_arr = call_getter_ref(chara_class, chara, "get_TrainingLevelInfoArray");
    }
    if is_valid_user_ptr(tl_arr) {
        let tl_len = seq_len(tl_arr);
        if tl_len > 0 && tl_len < 64 {
            for i in 0..tl_len {
                let ep = seq_item(tl_arr, i);
                if !is_valid_user_ptr(ep) {
                    continue;
                }
                let b = ep as *const u8;
                let cid = std::ptr::read_unaligned::<i32>(b.add(IL2CPP_COMMAND_ID_OFF) as *const i32);
                let lv = std::ptr::read_unaligned::<i32>(b.add(IL2CPP_COMMAND_LEVEL_OFF) as *const i32);
                if !(1..=5).contains(&lv) {
                    continue;
                }
                let idx = match cid {
                    101 | 601 => Some(0),
                    105 | 602 => Some(1),
                    102 | 603 => Some(2),
                    103 | 604 => Some(3),
                    106 | 605 => Some(4),
                    _ => None,
                };
                if let Some(k) = idx {
                    train_lv[k] = lv;
                    got_train_lv = true;
                }
            }
        }
    }
    let tl_count = [
        (train_lv[0] - 1).clamp(0, 4) * 4,
        (train_lv[1] - 1).clamp(0, 4) * 4,
        (train_lv[2] - 1).clamp(0, 4) * 4,
        (train_lv[3] - 1).clamp(0, 4) * 4,
        (train_lv[4] - 1).clamp(0, 4) * 4,
    ];
    let tl_total_json = if got_train_lv {
        format!(
            "[{},{},{},{},{}]",
            train_lv[0], train_lv[1], train_lv[2], train_lv[3], train_lv[4]
        )
    } else {
        "null".to_string()
    };

    if dataset_obj.is_null() {
        // 2.33.2 里 FestaDataSet 运行时不再绑定，输出完整 baseGame + 空 acts 缺省。
        return format!(
            r#"{{"festa":{{"acts":[],"maxActLevel":10,"campRemain":0,"trainLevelTotal":{},"persons":[],"checkPoints":[]}},"baseGame":{{"scenarioId":{},"umaId":{},"turn":{},"vital":{},"maxVital":{},"motivation":{},"fiveStatus":[{},{},{},{},{}],"fiveStatusLimit":[{},{},{},{},{}],"skillPt":{},"skillScore":0,"totalHints":0,"trainLevelCount":[{},{},{},{},{}],"ptScoreRate":1.0,"failureRateBias":0,"isIll":{},"isQieZhe":false,"isXiaoQie":false,"isAiJiao":false,"PositiveThinkingCount":0,"isRefreshMind":false,"LuckyCount":0,"zhongMaBlueCount":[0,0,0,0,0],"isRacing":false,"cardId":[{}],"persons":[{}],"personDistribution":[{}],"lockedTrainingId":-1,"friendship_noncard_yayoi":{},"friendship_noncard_reporter":{},"friend_stage":0,"friend_outgoingUsed":0,"playing_state":1,"source":"command","isAfterTraining":false,"charaEffects":[{}],"training":{{}}}},"notes":["dataset_missing"]}}"#,
            tl_total_json,
            sid,
            uma_id,
            turn,
            vital,
            max_vital,
            motivation,
            f[0], f[1], f[2], f[3], f[4],
            lim[0], lim[1], lim[2], lim[3], lim[4],
            skill_pt,
            tl_count[0], tl_count[1], tl_count[2], tl_count[3], tl_count[4],
            if is_ill_real { "true" } else { "false" },
            card_id_json,
            persons_json,
            dist_json,
            yayoi_bond,
            reporter_bond,
            eff_json
        );
    }'''

RECOMPUTE_OLD = r'''    // ★ ci_patch14: 若本帧为 skip 或无候选动作，绝不把 MT_W 全清 0，而是设 MT_STATE=0 退回原版权重
    if decision.chosen < 0 || decision.actions.is_empty() {
        MT_TURN.store(decision.turn, Release);
        MT_CHOSEN_TAG.store(-1, Release);
        MT_STATE.store(0, Release);
        mt_set_err(format!("skip: {}", decision.note));
        return;
    }

    // 计算训练/稽古候选分数区间 [min_score, max_score] 做差分化归一化
    let mut min_score = f64::INFINITY;
    let mut max_score = f64::NEG_INFINITY;
    for (tag, s) in decision.actions.iter() {
        if mt_slot(*tag).is_some() && s.is_finite() {
            if *s < min_score {
                min_score = *s;
            }
            if *s > max_score {
                max_score = *s;
            }
        }
    }
    let span = max_score - min_score;

    // 先在局部数组算完再一次性写入，消除渲染线程读到撕裂态的窗口
    let mut next_w = [0i32; 10];
    if let Some(best_slot) = mt_slot(decision.chosen_tag) {
        // 首选为训练/稽古位：次选按相对分映到 10..200，首选给 1000 压过 omakase_base_pt 条件加成 (+242)
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
    // 若首选不是训练/稽古（休息/外出/保健/比赛），保持 next_w 全 0，让原版休息/外出/比赛胜出

    for i in 0..MT_W.len() {
        MT_W[i].store(next_w[i], Release);
    }

    MT_TURN.store(decision.turn, Release);
    MT_CHOSEN_TAG.store(decision.chosen_tag, Release);
    MT_STATE.store(1, Release);
    mt_set_err(String::new());'''

RECOMPUTE_NEW = r'''    // ★ ci_patch15: 重复帧（同回合幂等 skip）直接保留当前 MT_STATE=1 与 MT_W，绝不清零！
    if decision.chosen < 0 || decision.actions.is_empty() {
        if decision.note.contains("重复帧") {
            return;
        }
        MT_TURN.store(decision.turn, Release);
        MT_CHOSEN_TAG.store(-1, Release);
        MT_STATE.store(0, Release);
        mt_set_err(format!("skip: {}", decision.note));
        return;
    }

    let has_act_cand = decision
        .actions
        .iter()
        .any(|(tag, _)| matches!(mt_slot(*tag), Some(5..=9)));

    // 计算参与接管的训练/稽古候选分数区间 [min_score, max_score]
    let mut min_score = f64::INFINITY;
    let mut max_score = f64::NEG_INFINITY;
    for (tag, s) in decision.actions.iter() {
        if let Some(slot) = mt_slot(*tag) {
            if (has_act_cand || slot < 5) && s.is_finite() {
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

    // 先在局部数组算完再一次性写入，消除渲染线程读到撕裂态的窗口
    let mut next_w = [0i32; 10];
    if !has_act_cand {
        // ★ 候选不含 ActTrain（acts 缺省）：稽古槽 5..10 全部置 -1（返回 None → 完整保留原版 200 稽古）
        for i in 5..10 {
            next_w[i] = -1;
        }
        if let Some(best_slot) = mt_slot(decision.chosen_tag) {
            if best_slot < 5 {
                // 普通训练：次选 10..70，首选 170（低于原版稽古 200 不抢稽古，但大幅压过次选普通训练）
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
        // 若首选是休息/外出/保健/比赛，next_w[0..5] 保持 0，next_w[5..10] 保持 -1
    } else if let Some(best_slot) = mt_slot(decision.chosen_tag) {
        // 未来补齐真实 acts 后：10 槽统一按分值接管
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

    for i in 0..MT_W.len() {
        MT_W[i].store(next_w[i], Release);
    }

    MT_TURN.store(decision.turn, Release);
    MT_CHOSEN_TAG.store(decision.chosen_tag, Release);
    MT_STATE.store(1, Release);
    mt_set_err(String::new());'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch15: {msg}")
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
        s = _replace_once(s, FRAME_TL_OLD, FRAME_TL_NEW, "frame_train_levels")
        s = _replace_once(s, RECOMPUTE_OLD, RECOMPUTE_NEW, "recompute_preserve_acts_and_dup")
    except RuntimeError as e:
        return _fail(str(e))

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    for needle in (
        "has_act_cand",
        'decision.note.contains("重复帧")',
        "next_w[best_slot] = 170;",
        "get_TrainingLevelInfoArray",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())
