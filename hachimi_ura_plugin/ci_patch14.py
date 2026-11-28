#!/usr/bin/env python3
"""CI 侧补丁 14：修复故障 #10（read_summary_inner 漏修 _charaEffectIdArray）+ 补齐 matsuri 在线帧字段与权重映射。

实测根因（2026-11-28，雷电 14 + 游戏 2.33.2，PID 1502）：
  1. [P0 · 故障 #10] ci_patch12 仅替换了 build_matsuri_frame() 中 CharaData+1080
     (_charaEffectIdArray) 的读取，漏掉了 read_summary_inner()（src/lib.rs:6068-6070）。
     马娘一旦带任意状态/病种（如 charaEffects=[47]），调用 /summary 或 /debug/turn_probe
     立即把 20 字节内联 ObscuredInt 当 8 字节指针解引用触发 SIGSEGV。
     → 同样替换为 read_inline_obscured_int_array(chara_effect_ids_arr)。

  2. [P1 · matsuri 帧解析失败与接管完善]
     - BaseStatus.playing_state 没有 #[serde(default)]，build_matsuri_frame() 漏输出导致
       parse_base_status() 100% 报 `帧解析失败`；
     - baseGame.cardId / persons / personDistribution 恒为空，会被 sync_deck / check_roster /
       has_decision_surface 拒绝或跳过；
     - 补齐从游戏内存安全读取（全指针 is_valid_user_ptr 校验）：
       * 6 张支援卡（按 position 1..=6 排序）→ baseGame.cardId
       * _evaluationList (+1016) 羁绊 + 理事长(102)/记者(103)羁绊 → baseGame.persons & friendship_noncard_*
       * HomeInfoData.CommandInfoArray → TrainingPartnerArray(+80) / TipsEventPartnerArray(+88)
         → baseGame.personDistribution (5x5 协议 ID 矩阵) & persons[].isHint
       * playing_state: 1, source: "command", isAfterTraining: false
       * isIll 仅在 charaEffects 含 1..=6 病种时为 true（不把 47 等增益误标为生病）
     - mt_recompute()：
       * TurnKind::Skipped / actions 为空时不写全 0 覆盖 MT_W，而是设 MT_STATE=0 退回原版权重；
       * 有明确训练/稽古决策时，非首选按 (score-min)/(max-min) 映到 10..200，首选位给 1000
         以压过游戏内 omakase_base_pt 的条件加分（最高 +242），确保 omakase 真按 AI 选择执行；
       * 若 matsuri 首选休息/外出/保健/比赛，则训练/稽古位写 0，让原版休息/外出/比赛胜出。
"""

import sys

PATH = "src/lib.rs"

SUMMARY_EFF_OLD = r'''    let chara_effect_ids_arr = read_ptr_at(chara_obj, 1080); // _charaEffectIdArray
    let chara_effect_ids: Vec<i32> = if !chara_effect_ids_arr.is_null() {
        read_il2cpp_int_list(chara_effect_ids_arr)
    } else {
        Vec::new()
    };'''

SUMMARY_EFF_NEW = r'''    // ★ ci_patch14: 修复故障 #10（CharaData+1080 _charaEffectIdArray 为 20 字节内联 ObscuredInt[]）
    let chara_effect_ids_arr = read_ptr_at(chara_obj, 1080); // _charaEffectIdArray
    let chara_effect_ids: Vec<i32> = if is_valid_user_ptr(chara_effect_ids_arr) {
        read_inline_obscured_int_array(chara_effect_ids_arr)
    } else {
        Vec::new()
    };'''

FRAME_MISSING_OLD = r'''    if dataset_obj.is_null() {
        // 2.33.2 里 FestaDataSet 运行时不再绑定（get_DataSet 与 backing field 都是 null），
        // 数据搬到 SingleMode.ScenarioFesta.* 的 Repository/Entity。
        // 直接调 ActListRepository.Get() 会让游戏崩，这条先不走。
        //
        // matsuri 的 restore_acts 是"逐条覆盖"：acts 为空时保持默认 gauge=0 / level=1，
        // 引擎注释明确写了 level==0 表示本帧未给 —— 所以空 acts 是合法缺省，不是假数据。
        // 代价：稽古/演目 Lv 相关估值偏低，补齐真实 acts 前不能拿稽古决策当真。
        return format!(
            r#"{{"festa":{{"acts":[],"maxActLevel":10,"campRemain":0,"trainLevelTotal":null,"persons":[],"checkPoints":[]}},"baseGame":{{"scenarioId":{},"umaId":{},"turn":{},"vital":{},"maxVital":{},"motivation":{},"fiveStatus":[{},{},{},{},{}],"fiveStatusLimit":[{},{},{},{},{}],"skillPt":{},"skillScore":0,"totalHints":0,"trainLevelCount":[0,0,0,0,0],"ptScoreRate":1.0,"failureRateBias":0,"isIll":{},"isQieZhe":false,"isXiaoQie":false,"isAiJiao":false,"PositiveThinkingCount":0,"isRefreshMind":false,"LuckyCount":0,"zhongMaBlueCount":[0,0,0,0,0],"isRacing":false,"cardId":[],"persons":[],"personDistribution":[],"lockedTrainingId":0,"friendship_noncard_yayoi":0,"friendship_noncard_reporter":0,"friend_stage":0,"friend_outgoingUsed":0,"charaEffects":[{}],"training":{{}}}},"notes":["dataset_missing"]}}"#,
            sid,
            uma_id,
            turn,
            vital,
            max_vital,
            motivation,
            f[0], f[1], f[2], f[3], f[4],
            lim[0], lim[1], lim[2], lim[3], lim[4],
            skill_pt,
            if eff_ids.is_empty() { "false" } else { "true" },
            eff_json
        );
    }'''

FRAME_MISSING_NEW = r'''    // ★ ci_patch14: 从游戏内存补齐 matsuri 必需的 cardId / persons / personDistribution / 羁绊
    let mut frame_cards: Vec<(i32, u32)> = Vec::new();
    let eq_arr = read_ptr_at(chara, 160);
    if is_valid_user_ptr(eq_arr) {
        let eq_n = seq_len(eq_arr);
        if eq_n > 0 && eq_n <= 16 {
            for i in 0..eq_n.min(8) {
                let e = seq_item(eq_arr, i);
                if !is_valid_user_ptr(e) {
                    continue;
                }
                let pos = read_obscured_int_at(e, 16);
                let sc = read_obscured_int_at(e, 36);
                let lb = read_obscured_int_at(e, 56);
                if (1..=6).contains(&pos) && sc > 0 && (0..=4).contains(&lb) {
                    frame_cards.push((pos, (sc * 10 + lb) as u32));
                }
            }
        }
    }
    frame_cards.sort_by_key(|&(pos, _)| pos);

    // 读 _evaluationList (+0x3f8) 羁绊：1..=6 支援卡、102 理事长、103 记者
    let mut bond_map: std::collections::HashMap<i32, i32> = std::collections::HashMap::new();
    let eval_list = read_ptr_at(chara, EVALUATION_LIST_OFF);
    if is_valid_user_ptr(eval_list) {
        let eval_cnt = read_int_at(eval_list, 0x18);
        let eval_items = read_ptr_at(eval_list, 0x10);
        if eval_cnt > 0 && eval_cnt < 500 && is_valid_user_ptr(eval_items) {
            let base = eval_items as *const u8;
            for i in 0..(eval_cnt as usize) {
                let item = std::ptr::read_unaligned::<*mut c_void>(
                    base.add(IL2CPP_LIST_ITEMS_OFF + i * IL2CPP_LIST_ITEM_SIZE)
                        as *const *mut c_void,
                );
                if !is_valid_user_ptr(item) {
                    continue;
                }
                let pid = read_obscured_int_at(item as *const c_void, EVALUATION_PARTNER_ID_OFF);
                let bond = read_obscured_int_at(item as *const c_void, EVALUATION_VALUE_OFF);
                if pid > 0 && pid < 100_000 && (0..=100).contains(&bond) {
                    bond_map.insert(pid, bond);
                }
            }
        }
    }
    let yayoi_bond = *bond_map.get(&102).unwrap_or(&0);
    let reporter_bond = *bond_map.get(&103).unwrap_or(&0);

    // 读 HomeInfoData.CommandInfoArray -> 5x5 personDistribution + 红点(tips)
    let mut dist: [Vec<i32>; 5] = [Vec::new(), Vec::new(), Vec::new(), Vec::new(), Vec::new()];
    let mut tips_set: std::collections::HashSet<i32> = std::collections::HashSet::new();
    let home_info = call_getter_ref(sm_class, sm, "get_HomeInfoData");
    if is_valid_user_ptr(home_info) {
        let hi_class = find_class(
            image,
            to_cstr("Gallop").as_ptr(),
            to_cstr("WorkSingleModeHomeInfoData").as_ptr(),
        );
        if !hi_class.is_null() {
            let cmd_arr = read_field_value(hi_class, home_info, "CommandInfoArray");
            if is_valid_user_ptr(cmd_arr) {
                let cmd_len = seq_len(cmd_arr);
                if cmd_len > 0 && cmd_len < 64 {
                    for i in 0..cmd_len {
                        let ep = seq_item(cmd_arr, i);
                        if !is_valid_user_ptr(ep) {
                            continue;
                        }
                        let cid = read_obscured_int_at(ep, 36);
                        let t_idx: Option<usize> = match cid {
                            101 | 601 | 1101 | 2101 | 5101 => Some(0),
                            105 | 602 | 1102 | 2102 | 5102 => Some(1),
                            102 | 603 | 1103 | 2103 | 5103 => Some(2),
                            103 | 604 | 1104 | 2104 | 5104 => Some(3),
                            106 | 605 | 1105 | 2105 | 5105 => Some(4),
                            _ => None,
                        };
                        let Some(idx) = t_idx else { continue };
                        let tips_arr = read_ptr_at(ep, TIPS_EVENT_PARTNER_ARRAY_OFF);
                        if is_valid_user_ptr(tips_arr) {
                            for tid in read_inline_obscured_int_array(tips_arr) {
                                if tid > 0 {
                                    tips_set.insert(tid);
                                }
                            }
                        }
                        let tp_arr = read_ptr_at(ep, TRAINING_PARTNER_ARRAY_OFF);
                        if is_valid_user_ptr(tp_arr) {
                            for pid in read_inline_obscured_int_array(tp_arr) {
                                let mapped = if (1..=6).contains(&pid) {
                                    pid - 1
                                } else if pid == 102 {
                                    6
                                } else if pid == 103 {
                                    7
                                } else if pid >= 1000 {
                                    pid
                                } else {
                                    continue;
                                };
                                if !dist[idx].contains(&mapped) && dist[idx].len() < 5 {
                                    dist[idx].push(mapped);
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    let card_id_json = frame_cards
        .iter()
        .map(|(_, c)| c.to_string())
        .collect::<Vec<_>>()
        .join(",");
    let persons_json = frame_cards
        .iter()
        .map(|&(pos, _)| {
            let bond = *bond_map.get(&pos).unwrap_or(&0);
            let is_hint = tips_set.contains(&pos);
            format!(
                r#"{{"personType":0,"charaId":0,"friendship":{},"isHint":{}}}"#,
                bond, is_hint
            )
        })
        .collect::<Vec<_>>()
        .join(",");
    let dist_json = dist
        .iter()
        .map(|row| {
            let mut padded = [-1i32; 5];
            for (j, &v) in row.iter().take(5).enumerate() {
                padded[j] = v;
            }
            format!(
                "[{},{},{},{},{}]",
                padded[0], padded[1], padded[2], padded[3], padded[4]
            )
        })
        .collect::<Vec<_>>()
        .join(",");
    let is_ill_real = eff_ids.iter().any(|&x| (1..=6).contains(&x));

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

RECOMPUTE_WEIGHT_OLD = r'''    // 找最大分做归一化基准
    let mut max_score = f64::NEG_INFINITY;
    for (_, s) in decision.actions.iter() {
        if s.is_finite() && *s > max_score {
            max_score = *s;
        }
    }

    // 先在局部数组算完再一次性写入，消除渲染线程读到“全 0 撕裂态”的窗口
    let mut next_w = [0i32; 10];
    for (tag, score) in decision.actions.iter() {
        if let Some(slot) = mt_slot(*tag) {
            let w = if max_score.is_finite() && max_score > 0.0 && score.is_finite() {
                (10.0 + 190.0 * (score / max_score)).round() as i32
            } else {
                10
            };
            next_w[slot] = w.clamp(0, 200);
        }
    }
    for i in 0..MT_W.len() {
        MT_W[i].store(next_w[i], Release);
    }

    MT_TURN.store(decision.turn, Release);
    MT_CHOSEN_TAG.store(decision.chosen_tag, Release);
    MT_STATE.store(1, Release);
    mt_set_err(String::new());'''

RECOMPUTE_WEIGHT_NEW = r'''    // ★ ci_patch14: 若本帧为 skip 或无候选动作，绝不把 MT_W 全清 0，而是设 MT_STATE=0 退回原版权重
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


def _fail(msg: str) -> int:
    print(f"::error::ci_patch14: {msg}")
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
        s = _replace_once(s, SUMMARY_EFF_OLD, SUMMARY_EFF_NEW, "read_summary_chara_effect_ids")
        s = _replace_once(s, FRAME_MISSING_OLD, FRAME_MISSING_NEW, "build_matsuri_frame_complete")
        s = _replace_once(s, RECOMPUTE_WEIGHT_OLD, RECOMPUTE_WEIGHT_NEW, "mt_recompute_weights")
    except RuntimeError as e:
        return _fail(str(e))

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    for needle in (
        "read_inline_obscured_int_array(chara_effect_ids_arr)",
        "\"playing_state\":1",
        "next_w[best_slot] = 1000;",
        "personDistribution",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")
    if "read_il2cpp_int_list(chara_effect_ids_arr)" in s:
        return _fail("read_il2cpp_int_list(chara_effect_ids_arr) 仍残留")
    return 0


if __name__ == "__main__":
    sys.exit(main())
