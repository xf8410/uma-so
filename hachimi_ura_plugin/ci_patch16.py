#!/usr/bin/env python3
"""CI 侧补丁 16：三大提分核心升级（① 消除慢一拍旧权重 + ② 接通 2.33.2 真实 FestaDataSet + ③ 配合轻量 MCTS 搜索）。

实测根因与验证（2026-11-28，雷电 14 + 游戏 2.33.2）：
  ① 消除「慢一拍用旧权重」：
     - 新增 `mt_peek_turn_fast()`（零 invoke、纯内存直读 WDM+96→SM+68，耗时 <2μs，带 klass 校验）。
     - `matsuri-worker` 改由 `Condvar` (`MT_WAKE`) 驱动 + 活跃期 200ms 轮询提前发现回合推进，
       同一回合/同局号已算好时不再重复跑 MCTS（零无用算力消耗）。
     - `mt_weight_for_arg(arg)` 严格校验 `MT_TURN == cur_turn`：
       * 已就绪且回合号一致 → 直接返回当前回合权重（<1μs）；
       * 回合号落后 → 立即唤醒 worker 并有界等待 `MT_DONE`（上限 `wait_budget_ms()`，默认 1500ms），
         一旦 worker 完成本回合（成功或 skip/error）立即返回；
       * `matsuri_bridge::ready()` 为 false（首次 7.3MB 加载期）或超时未对齐 → 返回 None 退回原版，
         **绝对不复用上回合旧权重**。
     - 同步优化 `mt_read_identity` 与 `build_matsuri_frame` 全部走直读偏移（WDM+96 / SM+88 / SM+112 /
       Chara+736 / Chara+1232 / ScenarioFesta+16），避免后台线程 `il2cpp_runtime_invoke` 锁竞争。

  ② 接通 2.33.2 真实 `festa.acts` + `festa.persons` + `friend_stage` + `checkPoints` + `kensan`：
     - 实测反汇编 `ActListRepository.get_FestaDataSet()` 发现 2.33.2 **并未废弃 `WorkSingleModeScenarioFestaDataSet`**！
       此前 `dataset_null` 是因为 `try_get_scenario_obj()` -> `find_scenario_getter_by_metadata()` 错取了
       第一个匹配的 `WorkScenarioURA`（Chara+0x488），而 `<ScenarioFesta>` 实际在 **Chara+0x4d0 (1232)**，
       其 `+0x10` 即为实时有效的 `WorkSingleModeScenarioFestaDataSet`！零新增 Dobby hook！
     - 完整解析并回填：
       * `FestaInfo (+16)`：`campRemain (+16)`、`maxActLevel (+36)`
       * `AdviceInfoArray (+40)`：`adviceId (+16) -> fusionAdviceId (+36)` 転成映射
       * `ActInfoArray (+48)`：5 条演目 `actType / gauge / level / needGauge / adviceId / fusionId / outingGainLevel`
       * `EvaluationInfoArray (+32)`：`festa.persons`（仅 `actType > 0`，`1..=6` 标 `card`、`>=1000` 标 `guest`）
       * `CheckPointResultArray (+56)`：`checkPoints` (`scheduleId / resultType`)
       * `CommandInfoArray (+24)`：`IsUpperTrainingEventOccurred (+72, ObscuredBool)` → `kensan[5]`
       * `_charaEffectIdArray (+1080)`：状态 `47` → `assistSupportLeft`、状态 `45` → `fatigueRecovery`
       * `_evaluationList (+1016)`：友人位（`1..=6` 且 `+112 CharaId >= 9000`）读取 `+56 IsOuting (ObscuredBool)` 与
         `+68 StoryStep (ObscuredInt)` → 真实 `friend_stage` (0/1/2/3) 与 `friend_outgoingUsed` (0..5)
       * `CharaData +16 (_id)` → `single_mode_chara_id`（换局自动重置 epoch）
     - 真实 Festa 数据就绪后，5 普通训练 + 5 演目稽古统一在 10 槽同标尺统筹，**首选拉至 `1000`** 彻底压死 `+242` 原版噪声；
       若 AI 首选休息/外出/保健/比赛，10 槽全置 `0` 放行。
"""

import sys

PATH = "src/lib.rs"

# 1) 在 build_matsuri_frame 前新增 read_obscured_bool_at 辅助函数，并升级 sm/home_info/festa/friend 读取
FRAME_HEAD_OLD = r'''unsafe fn build_matsuri_frame() -> String {
    if API.is_null() {
        return r#"{"error":"api_null"}"#.to_string();
    }
    let image = get_image();
    if image.is_null() {
        return r#"{"error":"image_null"}"#.to_string();
    }

    let wdm_class = find_class(
        image,
        to_cstr("Gallop").as_ptr(),
        to_cstr("WorkDataManager").as_ptr(),
    );
    if wdm_class.is_null() {
        return r#"{"error":"no_wdm"}"#.to_string();
    }
    let wdm = get_singleton(wdm_class);
    if wdm.is_null() {
        return r#"{"error":"no_wdm_inst"}"#.to_string();
    }

    let sm_class = find_class(
        image,
        to_cstr("Gallop").as_ptr(),
        to_cstr("WorkSingleModeData").as_ptr(),
    );
    if sm_class.is_null() {
        return r#"{"error":"no_sm_class"}"#.to_string();
    }
    ensure_il2cpp_thread_attached();
    let sm = call_getter_ref(wdm_class, wdm, "get_SingleMode");'''

FRAME_HEAD_NEW = r'''/// ★ ci_patch16: 解码 CodeStage ACTk ObscuredBool（12 字节：+0 key:u8, +4 hidden:i32；181=false, 213=true）
unsafe fn read_obscured_bool_at(obj: *const c_void, off: i32) -> bool {
    if !is_valid_user_ptr(obj) {
        return false;
    }
    let p = (obj as *const u8).add(off as usize);
    let key = std::ptr::read_unaligned::<u8>(p) as i32;
    let hidden = std::ptr::read_unaligned::<i32>(p.add(4) as *const i32);
    let dec = hidden ^ key;
    dec != 181 && dec != 0
}

unsafe fn build_matsuri_frame() -> String {
    if API.is_null() {
        return r#"{"error":"api_null"}"#.to_string();
    }
    let image = get_image();
    if image.is_null() {
        return r#"{"error":"image_null"}"#.to_string();
    }

    let wdm_class = find_class(
        image,
        to_cstr("Gallop").as_ptr(),
        to_cstr("WorkDataManager").as_ptr(),
    );
    if wdm_class.is_null() {
        return r#"{"error":"no_wdm"}"#.to_string();
    }
    let wdm = get_singleton(wdm_class);
    if !is_valid_user_ptr(wdm) {
        return r#"{"error":"no_wdm_inst"}"#.to_string();
    }

    let sm_class = find_class(
        image,
        to_cstr("Gallop").as_ptr(),
        to_cstr("WorkSingleModeData").as_ptr(),
    );
    if sm_class.is_null() {
        return r#"{"error":"no_sm_class"}"#.to_string();
    }
    ensure_il2cpp_thread_attached();
    // ★ ci_patch16: 优先直读 WorkDataManager+96 (<SingleMode>)，零 il2cpp_runtime_invoke
    let mut sm = read_ptr_at(wdm, 96);
    if !is_valid_user_ptr(sm) {
        sm = call_getter_ref(wdm_class, wdm, "get_SingleMode");
    }'''

# 2) 替换 build_matsuri_frame 中段 + 尾部：直读 Chara+1232 ScenarioFesta -> +16 DataSet，
#    读取真实 acts / fusionId / persons / checkPoints / kensan / friend_stage / friend_outgoingUsed / single_mode_chara_id
FRAME_BODY_OLD = r'''    // ---- baseGame：马娘与五维 ----
    let sid = read_obscured_int_at(chara, 568);
    let uma_id = read_obscured_int_at(chara, 36);
    let vital = read_obscured_int_at(chara, 208);
    let max_vital = read_obscured_int_at(chara, 228);
    let f = [
        read_obscured_int_at(chara, 248),
        read_obscured_int_at(chara, 268),
        read_obscured_int_at(chara, 288),
        read_obscured_int_at(chara, 308),
        read_obscured_int_at(chara, 328),
    ];
    let lim = [
        read_obscured_int_at(chara, 348),
        read_obscured_int_at(chara, 368),
        read_obscured_int_at(chara, 388),
        read_obscured_int_at(chara, 408),
        read_obscured_int_at(chara, 428),
    ];
    let skill_pt = read_obscured_int_at(chara, 704);
    let motivation = read_obscured_int_at(chara, 1056);
    let turn = raw_turn - 1;

    let eff_arr = read_ptr_at(chara, 1080);
    let eff_ids: Vec<i32> = if !is_valid_user_ptr(eff_arr) {
        Vec::new()
    } else {
        read_inline_obscured_int_array(eff_arr)
    };
    let eff_json = eff_ids
        .iter()
        .map(|x| x.to_string())
        .collect::<Vec<_>>()
        .join(",");

    // ---- festa：剧本 15 数据段 ----
    let scenario_class_name = match sid {
        15 => "WorkSingleModeScenarioFesta",
        _ => "Unknown",
    };
    let scenario_class = find_class_by_short_name(image, scenario_class_name);
    let scenario_obj = try_get_scenario_obj(chara_class, chara, sid);
    let mut dataset_obj: *mut c_void = std::ptr::null_mut();
    if !scenario_class.is_null() && !scenario_obj.is_null() {
        dataset_obj = call_getter_ref(scenario_class, scenario_obj, "get_DataSet");
    }
    // ★ ci_patch14: 从游戏内存补齐 matsuri 必需的 cardId / persons / personDistribution / 羁绊
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
    }

    // FestaInfo(+16)：+16 合宿剩余、+36 演目上限
    let fi = read_ptr_at(dataset_obj, 16);
    let (camp_remain, max_act_level) = if fi.is_null() {
        (0, 10)
    } else {
        (read_obscured_int_at(fi, 16), read_obscured_int_at(fi, 36))
    };

    // ActInfoArray(+48)：演目五条
    let acts_arr = read_ptr_at(dataset_obj, 48);
    let mut acts: Vec<String> = Vec::new();
    for i in 0..seq_len(acts_arr).min(8) {
        let e = seq_item(acts_arr, i);
        if e.is_null() {
            continue;
        }
        acts.push(format!(
            r#"{{"actType":{},"gauge":{},"level":{},"needGauge":{},"actTrainingState":{},"adviceId":{}}}"#,
            read_obscured_int_at(e, 16),
            read_obscured_int_at(e, 36),
            read_obscured_int_at(e, 56),
            read_obscured_int_at(e, 76),
            read_obscured_int_at(e, 96),
            read_obscured_int_at(e, 116)
        ));
    }

    // EvaluationInfoArray(+32)：人头（targetId / charaId / actType）
    let eval_arr = read_ptr_at(dataset_obj, 32);
    let mut festa_persons: Vec<String> = Vec::new();
    for i in 0..seq_len(eval_arr).min(32) {
        let e = seq_item(eval_arr, i);
        if e.is_null() {
            continue;
        }
        festa_persons.push(format!(
            r#"{{"targetId":{},"charaId":{},"actType":{}}}"#,
            read_obscured_int_at(e, 16),
            read_obscured_int_at(e, 36),
            read_obscured_int_at(e, 56)
        ));
    }

    // CheckPointResultArray(+56)：発表会 / 本番结果
    let cp_arr = read_ptr_at(dataset_obj, 56);
    let mut check_points: Vec<String> = Vec::new();
    for i in 0..seq_len(cp_arr).min(8) {
        let e = seq_item(cp_arr, i);
        if e.is_null() {
            continue;
        }
        check_points.push(format!(
            r#"{{"scheduleId":{},"resultType":{}}}"#,
            read_obscured_int_at(e, 16),
            read_obscured_int_at(e, 36)
        ));
    }

    // CommandInfoArray(+24) → baseGame.training（引擎只用于 --verify-training 验算）
    // ParamsIncDecInfo：+16 Value、+36 BonusValue；7 项定长 = 速/耐/力/根/智/PT/体力
    let cmds_arr = read_ptr_at(dataset_obj, 24);
    let mut training: Vec<String> = Vec::new();
    for i in 0..seq_len(cmds_arr).min(16) {
        let e = seq_item(cmds_arr, i);
        if e.is_null() {
            continue;
        }
        let cid = read_obscured_int_at(e, 36);
        if cid < 0 {
            continue;
        }
        let garr = read_ptr_at(e, 56);
        let mut lower: Vec<String> = Vec::new();
        let mut upper: Vec<String> = Vec::new();
        for j in 0..seq_len(garr).min(7) {
            let ge = seq_item(garr, j);
            if ge.is_null() {
                continue;
            }
            lower.push(read_obscured_int_at(ge, 16).to_string());
            upper.push(read_obscured_int_at(ge, 36).to_string());
        }
        if !lower.is_empty() {
            training.push(format!(
                r#""{}":{{"failureRate":0.0,"lower":[{}],"upper":[{}]}}"#,
                cid,
                lower.join(","),
                upper.join(",")
            ));
        }
    }

    format!(
        r#"{{"festa":{{"acts":[{}],"maxActLevel":{},"campRemain":{},"trainLevelTotal":null,"persons":[{}],"checkPoints":[{}]}},"baseGame":{{"scenarioId":{},"umaId":{},"turn":{},"vital":{},"maxVital":{},"motivation":{},"fiveStatus":[{},{},{},{},{}],"fiveStatusLimit":[{},{},{},{},{}],"skillPt":{},"skillScore":0,"totalHints":0,"trainLevelCount":[0,0,0,0,0],"ptScoreRate":1.0,"failureRateBias":0,"isIll":{},"isQieZhe":false,"isXiaoQie":false,"isAiJiao":false,"PositiveThinkingCount":0,"isRefreshMind":false,"LuckyCount":0,"zhongMaBlueCount":[0,0,0,0,0],"isRacing":false,"cardId":[],"persons":[],"personDistribution":[],"lockedTrainingId":0,"friendship_noncard_yayoi":0,"friendship_noncard_reporter":0,"friend_stage":0,"friend_outgoingUsed":0,"charaEffects":[{}],"training":{{{}}}}}}}"#,
        acts.join(","),
        max_act_level,
        camp_remain,
        festa_persons.join(","),
        check_points.join(","),
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
        eff_json,
        training.join(",")
    )
}'''

FRAME_BODY_NEW = r'''    // ---- baseGame：马娘与五维 ----
    let sm_chara_id = read_obscured_int_at(chara, 16);
    let sid = read_obscured_int_at(chara, 568);
    let uma_id = read_obscured_int_at(chara, 36);
    let vital = read_obscured_int_at(chara, 208);
    let max_vital = read_obscured_int_at(chara, 228);
    let f = [
        read_obscured_int_at(chara, 248),
        read_obscured_int_at(chara, 268),
        read_obscured_int_at(chara, 288),
        read_obscured_int_at(chara, 308),
        read_obscured_int_at(chara, 328),
    ];
    let lim = [
        read_obscured_int_at(chara, 348),
        read_obscured_int_at(chara, 368),
        read_obscured_int_at(chara, 388),
        read_obscured_int_at(chara, 408),
        read_obscured_int_at(chara, 428),
    ];
    let skill_pt = read_obscured_int_at(chara, 704);
    let motivation = read_obscured_int_at(chara, 1056);
    let turn = raw_turn - 1;

    let eff_arr = read_ptr_at(chara, 1080);
    let eff_ids: Vec<i32> = if !is_valid_user_ptr(eff_arr) {
        Vec::new()
    } else {
        read_inline_obscured_int_array(eff_arr)
    };
    let eff_json = eff_ids
        .iter()
        .map(|x| x.to_string())
        .collect::<Vec<_>>()
        .join(",");
    let is_ill_real = eff_ids.iter().any(|&x| (1..=6).contains(&x));
    let assist_support_left = if eff_ids.contains(&47) { 1 } else { 0 };
    let fatigue_recovery = eff_ids.contains(&45);

    // ★ ci_patch16: 2.33.2 实测 WorkSingleModeCharaData+1232 (0x4d0) = <ScenarioFesta>k__BackingField，
    //   其 +16 (0x10) 即为实时有效的 WorkSingleModeScenarioFestaDataSet（之前误取了 +1160 WorkScenarioURA）！
    let mut dataset_obj: *mut c_void = std::ptr::null_mut();
    if sid == 15 {
        let mut festa_obj = read_ptr_at(chara, 1232);
        let mut festa_ok = false;
        if is_valid_user_ptr(festa_obj) {
            let fk = std::ptr::read_unaligned::<*mut c_void>(festa_obj as *const *mut c_void);
            if is_valid_user_ptr(fk)
                && get_class_name_from_pointer(fk).contains("WorkSingleModeScenarioFesta")
            {
                festa_ok = true;
            }
        }
        if !festa_ok && !chara_class.is_null() {
            festa_obj = call_getter_ref(chara_class, chara, "get_ScenarioFesta");
            if is_valid_user_ptr(festa_obj) {
                let fk = std::ptr::read_unaligned::<*mut c_void>(festa_obj as *const *mut c_void);
                if is_valid_user_ptr(fk)
                    && get_class_name_from_pointer(fk).contains("WorkSingleModeScenarioFesta")
                {
                    festa_ok = true;
                }
            }
        }
        if festa_ok {
            let ds = read_ptr_at(festa_obj, 16);
            if is_valid_user_ptr(ds) {
                let dk = std::ptr::read_unaligned::<*mut c_void>(ds as *const *mut c_void);
                if is_valid_user_ptr(dk)
                    && get_class_name_from_pointer(dk).contains("WorkSingleModeScenarioFestaDataSet")
                {
                    dataset_obj = ds;
                }
            }
        }
    }

    // 读取 6 张支援卡（按 position 1..=6 排序）
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

    // 读 _evaluationList (+0x3f8)：羁绊 + 友人出行状态（+56 IsOuting ObscuredBool, +68 StoryStep ObscuredInt, +112 CharaId）
    let mut bond_map: std::collections::HashMap<i32, i32> = std::collections::HashMap::new();
    let mut friend_stage: i32 = 0;
    let mut friend_outgoing_used: i32 = 0;
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
                if (1..=6).contains(&pid) {
                    let ev_cid = read_obscured_int_at(item as *const c_void, 112);
                    if ev_cid >= 9000 {
                        let is_out = read_obscured_bool_at(item as *const c_void, 56);
                        let step = read_obscured_int_at(item as *const c_void, 68);
                        if (0..=10).contains(&step) {
                            friend_outgoing_used = step.clamp(0, 5);
                        }
                        friend_stage = if is_out {
                            2
                        } else if friend_outgoing_used >= 5 {
                            3
                        } else if bond > 30 {
                            1
                        } else {
                            0
                        };
                    }
                }
            }
        }
    }
    let yayoi_bond = *bond_map.get(&102).unwrap_or(&0);
    let reporter_bond = *bond_map.get(&103).unwrap_or(&0);

    // 读 HomeInfoData (+112) -> CommandInfoArray -> 5x5 personDistribution + 红点(tips)
    let mut dist: [Vec<i32>; 5] = [Vec::new(), Vec::new(), Vec::new(), Vec::new(), Vec::new()];
    let mut tips_set: std::collections::HashSet<i32> = std::collections::HashSet::new();
    let mut home_info = read_ptr_at(sm, 112);
    if !is_valid_user_ptr(home_info) {
        home_info = call_getter_ref(sm_class, sm, "get_HomeInfoData");
    }
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

    // 读取 5 项训练设施等级（CharaData+736 <TrainingLevelInfoArray>）
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

    let epoch_field = if sm_chara_id > 0 {
        format!(r#","single_mode_chara_id":{}"#, sm_chara_id)
    } else {
        String::new()
    };

    // ---- 解析真实 WorkSingleModeScenarioFestaDataSet（若存在）----
    let mut camp_remain: i32 = 0;
    let mut max_act_level: i32 = 10;
    let mut acts: Vec<String> = Vec::new();
    let mut festa_persons: Vec<String> = Vec::new();
    let mut check_points: Vec<String> = Vec::new();
    let mut kensan = [false; 5];
    let has_dataset = is_valid_user_ptr(dataset_obj);

    if has_dataset {
        // FestaInfo(+16)：+16 合宿剩余、+36 演目上限
        let fi = read_ptr_at(dataset_obj, 16);
        if is_valid_user_ptr(fi) {
            let cr = read_obscured_int_at(fi, 16);
            let mal = read_obscured_int_at(fi, 36);
            if (0..=10).contains(&cr) {
                camp_remain = cr;
            }
            if (1..=20).contains(&mal) {
                max_act_level = mal;
            }
        }

        // AdviceInfoArray(+40)：adviceId(+16) -> fusionAdviceId(+36)
        let mut fusion_map: std::collections::HashMap<i32, i32> = std::collections::HashMap::new();
        let adv_arr = read_ptr_at(dataset_obj, 40);
        if is_valid_user_ptr(adv_arr) {
            let adv_n = seq_len(adv_arr);
            if adv_n > 0 && adv_n <= 32 {
                for i in 0..adv_n {
                    let e = seq_item(adv_arr, i);
                    if !is_valid_user_ptr(e) {
                        continue;
                    }
                    let aid = read_obscured_int_at(e, 16);
                    let fid = read_obscured_int_at(e, 36);
                    if aid > 0 && fid >= 0 {
                        fusion_map.insert(aid, fid);
                    }
                }
            }
        }

        // ActInfoArray(+48)：演目五条（actType/gauge/level/needGauge/adviceId/fusionId/outingGainLevel）
        let acts_arr = read_ptr_at(dataset_obj, 48);
        if is_valid_user_ptr(acts_arr) {
            let acts_n = seq_len(acts_arr);
            if acts_n > 0 && acts_n <= 16 {
                for i in 0..acts_n.min(8) {
                    let e = seq_item(acts_arr, i);
                    if !is_valid_user_ptr(e) {
                        continue;
                    }
                    let act_type = read_obscured_int_at(e, 16);
                    if !(1..=5).contains(&act_type) {
                        continue;
                    }
                    let gauge = read_obscured_int_at(e, 36).clamp(0, 20);
                    let level = read_obscured_int_at(e, 56).clamp(0, 20);
                    let need_gauge = read_obscured_int_at(e, 76).clamp(1, 20);
                    let advice_id = read_obscured_int_at(e, 116);
                    let fusion_id = *fusion_map.get(&advice_id).unwrap_or(&0);
                    let outing_gain = read_obscured_int_at(e, 136).clamp(0, 5);
                    acts.push(format!(
                        r#"{{"actType":{},"gauge":{},"level":{},"needGauge":{},"adviceId":{},"fusionId":{},"outingGainLevel":{}}}"#,
                        act_type, gauge, level, need_gauge, advice_id, fusion_id, outing_gain
                    ));
                }
            }
        }

        // EvaluationInfoArray(+32)：人头担当（仅输出 actType > 0，避免把友人/NPC 占位当 card）
        let eval_arr = read_ptr_at(dataset_obj, 32);
        if is_valid_user_ptr(eval_arr) {
            let ev_n = seq_len(eval_arr);
            if ev_n > 0 && ev_n <= 64 {
                for i in 0..ev_n {
                    let e = seq_item(eval_arr, i);
                    if !is_valid_user_ptr(e) {
                        continue;
                    }
                    let tid = read_obscured_int_at(e, 16);
                    let cid = read_obscured_int_at(e, 36);
                    let act_t = read_obscured_int_at(e, 56);
                    if !(1..=5).contains(&act_t) {
                        continue;
                    }
                    let origin = if (1..=6).contains(&tid) {
                        if cid >= 9000 {
                            continue;
                        }
                        "card"
                    } else if tid >= 1000 {
                        "guest"
                    } else {
                        continue;
                    };
                    festa_persons.push(format!(
                        r#"{{"targetId":{},"actType":{},"origin":"{}"}}"#,
                        tid, act_t, origin
                    ));
                }
            }
        }

        // CheckPointResultArray(+56)：発表会 / 本番结果
        let cp_arr = read_ptr_at(dataset_obj, 56);
        if is_valid_user_ptr(cp_arr) {
            let cp_n = seq_len(cp_arr);
            if cp_n > 0 && cp_n <= 16 {
                for i in 0..cp_n.min(8) {
                    let e = seq_item(cp_arr, i);
                    if !is_valid_user_ptr(e) {
                        continue;
                    }
                    let sched = read_obscured_int_at(e, 16);
                    let rtype = read_obscured_int_at(e, 36);
                    if (1..=3).contains(&sched) && (0..=3).contains(&rtype) {
                        check_points.push(format!(
                            r#"{{"scheduleId":{},"resultType":{}}}"#,
                            sched, rtype
                        ));
                    }
                }
            }
        }

        // CommandInfoArray(+24)：IsUpperTrainingEventOccurred(+72 ObscuredBool) -> kensan[5]
        let cmds_arr = read_ptr_at(dataset_obj, 24);
        if is_valid_user_ptr(cmds_arr) {
            let cmd_n = seq_len(cmds_arr);
            if cmd_n > 0 && cmd_n <= 32 {
                for i in 0..cmd_n.min(16) {
                    let e = seq_item(cmds_arr, i);
                    if !is_valid_user_ptr(e) {
                        continue;
                    }
                    let cid = read_obscured_int_at(e, 36);
                    let idx: Option<usize> = match cid {
                        101 | 601 | 5101 => Some(0),
                        105 | 602 | 5102 => Some(1),
                        102 | 603 | 5103 => Some(2),
                        103 | 604 | 5104 => Some(3),
                        106 | 605 | 5105 => Some(4),
                        _ => None,
                    };
                    if let Some(k) = idx {
                        if read_obscured_bool_at(e, 72) {
                            kensan[k] = true;
                        }
                    }
                }
            }
        }
    }

    let notes_json = if has_dataset && !acts.is_empty() {
        r#"["festa_dataset_live"]"#
    } else {
        r#"["dataset_missing"]"#
    };

    format!(
        r#"{{"festa":{{"acts":[{}],"maxActLevel":{},"campRemain":{},"assistSupportLeft":{},"fatigueRecovery":{},"trainLevelTotal":{},"kensan":[{},{},{},{},{}],"persons":[{}],"checkPoints":[{}]}},"baseGame":{{"scenarioId":{},"umaId":{},"turn":{},"vital":{},"maxVital":{},"motivation":{},"fiveStatus":[{},{},{},{},{}],"fiveStatusLimit":[{},{},{},{},{}],"skillPt":{},"skillScore":0,"totalHints":0,"trainLevelCount":[{},{},{},{},{}],"ptScoreRate":1.0,"failureRateBias":0,"isIll":{},"isQieZhe":false,"isXiaoQie":false,"isAiJiao":false,"PositiveThinkingCount":0,"isRefreshMind":false,"LuckyCount":0,"zhongMaBlueCount":[0,0,0,0,0],"isRacing":false,"cardId":[{}],"persons":[{}],"personDistribution":[{}],"lockedTrainingId":-1,"friendship_noncard_yayoi":{},"friendship_noncard_reporter":{},"friend_stage":{},"friend_outgoingUsed":{},"playing_state":1,"source":"command","isAfterTraining":false{},"charaEffects":[{}],"training":{{}}}},"notes":{}}}"#,
        acts.join(","),
        max_act_level,
        camp_remain,
        assist_support_left,
        if fatigue_recovery { "true" } else { "false" },
        tl_total_json,
        kensan[0], kensan[1], kensan[2], kensan[3], kensan[4],
        festa_persons.join(","),
        check_points.join(","),
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
        friend_stage,
        friend_outgoing_used,
        epoch_field,
        eff_json,
        notes_json
    )
}'''

# 3) mt_read_identity 也优先直读 WDM+96，零 invoke
ID_SM_OLD = r'''    ensure_il2cpp_thread_attached();
    let sm = call_getter_ref(wdm_class, wdm, "get_SingleMode");'''

ID_SM_NEW = r'''    ensure_il2cpp_thread_attached();
    let mut sm = read_ptr_at(wdm, 96);
    if !is_valid_user_ptr(sm) {
        sm = call_getter_ref(wdm_class, wdm, "get_SingleMode");
    }'''

# 4) 升级 matsuri-worker + mt_recompute + mt_weight_for_arg + mt_json：
#    - 新增 mt_peek_turn_fast()（零 invoke，带 klass 校验）
#    - Condvar 即时唤醒 + 回合变化检测 + 有界等待 + 严禁跨回合复用旧权重
#    - 真实 festa 就绪时 10 槽同标尺统筹、首选拉到 1000 彻底压死 +242 噪声
WORKER_AND_HOOK_OLD = r'''/// 最近一次 GetBaseWeight 命中的时间戳（毫秒）；0 = 从未命中
static MT_LAST_HIT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

static MT_WORKER_ON: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

fn mt_start_worker() {
    use std::sync::atomic::Ordering::Relaxed;
    if MT_WORKER_ON.swap(true, Relaxed) {
        return;
    }
    let spawned = std::thread::Builder::new()
        .name("matsuri-worker".into())
        .stack_size(8 * 1024 * 1024)
        .spawn(|| {
            // 首轮让游戏先稳定，避开启动/加载的抖动期
            std::thread::sleep(std::time::Duration::from_secs(5));
            loop {
                // 门禁：只有最近 10 秒内 GetBaseWeight 被调用过（说明正在决策、
                // 游戏对象有效）才动游戏内存。标题画面/结算界面 hook 不命中，
                // 这里直接跳过 —— 上一版就是缺这道门禁，在标题画面读 Character 崩的。
                let last = MT_LAST_HIT.load(std::sync::atomic::Ordering::Relaxed);
                let idle = last == 0 || wb_now_ms().saturating_sub(last) > 10_000;
                if idle {
                    std::thread::sleep(std::time::Duration::from_millis(1000));
                    continue;
                }
                let r = std::panic::catch_unwind(|| unsafe { mt_recompute() });
                if r.is_err() {
                    if let Ok(mut g) = MT_ERR.lock() {
                        *g = format!("{} | worker panic", g);
                    }
                    MT_STATE.store(2, std::sync::atomic::Ordering::Relaxed);
                }
                std::thread::sleep(std::time::Duration::from_millis(3000));
            }
        });
    if spawned.is_err() {
        MT_WORKER_ON.store(false, Relaxed);
    }
}

unsafe fn mt_recompute() {
    use std::sync::atomic::Ordering::{Acquire, Relaxed, Release};

    // SIGSEGV 冷却期内不动游戏内存
    let now_s = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    if now_s < SIGSEGV_COOLDOWN_UNTIL.load(Acquire) {
        return;
    }

    ensure_il2cpp_thread_attached();

    // 仅在快速内存读取阶段持有 READ_MUTEX + 线程绑定 sigsetjmp 保护；
    // 拿到纯 Rust 字符串后立刻释锁，避免 100~800ms 决策计算阻塞 HTTP 端点。
    let (identity, frame) = {
        let _lock = READ_MUTEX.lock().unwrap_or_else(|e| e.into_inner());
        let jmp = sys_sigsetjmp(SIGSEGV_JMP_BUF.as_mut_ptr(), 1);
        if jmp != 0 {
            sigsegv_disarm();
            MT_STATE.store(2, Release);
            mt_set_err("sigsegv_recovered_in_worker".to_string());
            return;
        }
        sigsegv_arm();
        let id = mt_read_identity();
        let fr = if id.uma > 0 && id.cards.len() == 6 {
            build_matsuri_frame()
        } else {
            String::new()
        };
        sigsegv_disarm();
        (id, fr)
    };

    if identity.uma == 0 || identity.cards.len() != 6 {
        MT_STATE.store(2, Release);
        mt_set_err(format!(
            "identity_not_ready(uma={}, cards={:?})",
            identity.uma, identity.cards
        ));
        return;
    }

    // 身份变了才重建 session（重建会丢会话状态）
    let key = format!("{}:{:?}", identity.uma, identity.cards);
    let need_init = !matsuri_bridge::ready()
        || MT_ID_KEY
            .lock()
            .map(|g| g.as_str() != key.as_str())
            .unwrap_or(true);
    if need_init {
        match matsuri_bridge::init(identity.uma, &identity.cards, 61444) {
            Ok(()) => {
                if let Ok(mut g) = MT_ID_KEY.lock() {
                    *g = key;
                }
            }
            Err(e) => {
                MT_STATE.store(2, Release);
                mt_set_err(format!(
                    "init(uma={}, cards={:?}): {e}",
                    identity.uma, identity.cards
                ));
                return;
            }
        }
    }

    if frame.is_empty() || frame.contains("\"error\"") {
        MT_STATE.store(2, Release);
        mt_set_err(format!("frame: {}", frame.chars().take(120).collect::<String>()));
        return;
    }

    let decision = match matsuri_bridge::decide(&frame) {
        Ok(d) => d,
        Err(e) => {
            MT_STATE.store(2, Release);
            mt_set_err(format!("decide: {e}"));
            return;
        }
    };

    // ★ ci_patch15: 重复帧（同回合幂等 skip）直接保留当前 MT_STATE=1 与 MT_W，绝不清零！
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
    mt_set_err(String::new());
}

/// hook 用：给定 omakase 行号，返回 matsuri 的权重（None = 不接管）
unsafe fn mt_weight_for_arg(arg: i32) -> Option<i32> {
    use std::sync::atomic::Ordering::Relaxed;
    // 活跃信号：worker 靠这个判断"现在是不是在决策中"，别在标题画面乱读对象
    MT_LAST_HIT.store(wb_now_ms(), Relaxed);

    // 计算已移到 matsuri-worker 线程，这里只读缓存：
    // hook 跑在渲染线程，任何重活（Data::init 解析 7.3MB、建会话）都会闪退。

    if MT_STATE.load(std::sync::atomic::Ordering::Acquire) != 1 {
        return None;
    }
    let tag = matsuri_bridge::omakase_arg_to_tag(arg)?;
    let slot = mt_slot(tag)?;
    let w = MT_W[slot].load(std::sync::atomic::Ordering::Acquire);
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
}'''

WORKER_AND_HOOK_NEW = r'''/// 最近一次 GetBaseWeight 命中的时间戳（毫秒）；0 = 从未命中
static MT_LAST_HIT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static MT_EPOCH_ID: std::sync::atomic::AtomicI32 = std::sync::atomic::AtomicI32::new(-1);
static MT_ATTEMPTED_TURN: std::sync::atomic::AtomicI32 = std::sync::atomic::AtomicI32::new(-1);
static MT_ATTEMPTED_AT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static MT_STALE_SKIPS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static MT_SYNC_WAITS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static MT_SYNC_HITS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static MT_LAST_MS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static MT_FESTA_LIVE: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
static MT_PEEKED_TURN: std::sync::atomic::AtomicI32 = std::sync::atomic::AtomicI32::new(-1);
static MT_PEEKED_EPOCH: std::sync::atomic::AtomicI32 = std::sync::atomic::AtomicI32::new(-1);

static MT_WAKE: std::sync::OnceLock<(std::sync::Mutex<u64>, std::sync::Condvar)> =
    std::sync::OnceLock::new();
static MT_DONE: std::sync::OnceLock<(std::sync::Mutex<u64>, std::sync::Condvar)> =
    std::sync::OnceLock::new();

fn mt_wake_pair() -> &'static (std::sync::Mutex<u64>, std::sync::Condvar) {
    MT_WAKE.get_or_init(|| (std::sync::Mutex::new(0), std::sync::Condvar::new()))
}

fn mt_done_pair() -> &'static (std::sync::Mutex<u64>, std::sync::Condvar) {
    MT_DONE.get_or_init(|| (std::sync::Mutex::new(0), std::sync::Condvar::new()))
}

fn mt_notify_wake() {
    let (mu, cv) = mt_wake_pair();
    if let Ok(mut seq) = mu.lock() {
        *seq = seq.wrapping_add(1);
        cv.notify_all();
    }
}

fn mt_notify_done() {
    let (mu, cv) = mt_done_pair();
    if let Ok(mut seq) = mu.lock() {
        *seq = seq.wrapping_add(1);
        cv.notify_all();
    }
}

/// ★ ci_patch16: 零 invoke 快速读取当前 (turn_0based, single_mode_chara_id)
/// 仅读 WDM+96 -> SM+68 / SM+88 -> Chara+16，全程 is_valid_user_ptr + klass 校验，耗时 <2μs
unsafe fn mt_peek_turn_fast() -> (i32, i32) {
    if API.is_null() {
        return (-1, -1);
    }
    let image = get_image();
    if image.is_null() {
        return (-1, -1);
    }
    let wdm_class = find_class(
        image,
        to_cstr("Gallop").as_ptr(),
        to_cstr("WorkDataManager").as_ptr(),
    );
    if wdm_class.is_null() {
        return (-1, -1);
    }
    let wdm = get_singleton(wdm_class);
    if !is_valid_user_ptr(wdm) {
        return (-1, -1);
    }
    let sm = read_ptr_at(wdm, 96);
    if !is_valid_user_ptr(sm) {
        return (-1, -1);
    }
    let sm_klass = std::ptr::read_unaligned::<*mut c_void>(sm as *const *mut c_void);
    if !is_valid_user_ptr(sm_klass)
        || !get_class_name_from_pointer(sm_klass).contains("WorkSingleModeData")
    {
        return (-1, -1);
    }
    let raw_turn = read_obscured_int_at(sm, 68);
    if raw_turn <= 0 || raw_turn > 200 {
        return (-1, -1);
    }
    let mut epoch_id = -1i32;
    let chara = read_ptr_at(sm, 88);
    if is_valid_user_ptr(chara) {
        let ck = std::ptr::read_unaligned::<*mut c_void>(chara as *const *mut c_void);
        if is_valid_user_ptr(ck)
            && get_class_name_from_pointer(ck).contains("WorkSingleModeCharaData")
        {
            let cid = read_obscured_int_at(chara, 16);
            if cid > 0 {
                epoch_id = cid;
            }
        }
    }
    let res = (raw_turn - 1, epoch_id);
    MT_PEEKED_TURN.store(res.0, std::sync::atomic::Ordering::Release);
    if res.1 > 0 {
        MT_PEEKED_EPOCH.store(res.1, std::sync::atomic::Ordering::Release);
    }
    res
}

/// 后台线程安全版回合探针：受 SIGSEGV 冷却 + READ_MUTEX + sigsetjmp 保护
unsafe fn mt_peek_turn_protected() -> (i32, i32) {
    let now_s = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    if now_s < SIGSEGV_COOLDOWN_UNTIL.load(std::sync::atomic::Ordering::Acquire) {
        return (-1, -1);
    }
    ensure_il2cpp_thread_attached();
    let _lock = READ_MUTEX.lock().unwrap_or_else(|e| e.into_inner());
    let jmp = sys_sigsetjmp(SIGSEGV_JMP_BUF.as_mut_ptr(), 1);
    if jmp != 0 {
        sigsegv_disarm();
        return (-1, -1);
    }
    sigsegv_arm();
    let res = mt_peek_turn_fast();
    sigsegv_disarm();
    res
}

static MT_WORKER_ON: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

fn mt_start_worker() {
    use std::sync::atomic::Ordering::{Acquire, Relaxed};
    if MT_WORKER_ON.swap(true, Relaxed) {
        return;
    }
    let spawned = std::thread::Builder::new()
        .name("matsuri-worker".into())
        .stack_size(8 * 1024 * 1024)
        .spawn(|| {
            // 首轮让游戏先稳定，避开启动/加载的抖动期
            std::thread::sleep(std::time::Duration::from_secs(5));
            let mut last_wake_seq: u64 = 0;
            loop {
                // 门禁：最近 60 秒内 GetBaseWeight 被调用过才活动（跨动画/赛事/事件保持热态，
                // 能在下回合进入 Home 屏瞬间提前预算；标题画面/结算后 60 秒自动静默）
                let last = MT_LAST_HIT.load(Relaxed);
                let idle = last == 0 || wb_now_ms().saturating_sub(last) > 60_000;
                if idle {
                    let (mu, cv) = mt_wake_pair();
                    if let Ok(guard) = mu.lock() {
                        if *guard == last_wake_seq {
                            let (g2, _) = cv
                                .wait_timeout(guard, std::time::Duration::from_millis(1000))
                                .unwrap_or_else(|e| e.into_inner());
                            last_wake_seq = *g2;
                        } else {
                            last_wake_seq = *guard;
                        }
                    }
                    continue;
                }

                // 活跃态：受 sigsetjmp 保护快速检查回合/局号是否变化，避免同回合重复跑 MCTS
                let (cur_turn, cur_epoch) = unsafe { mt_peek_turn_protected() };
                let cached_turn = MT_TURN.load(Acquire);
                let cached_epoch = MT_EPOCH_ID.load(Acquire);
                let state = MT_STATE.load(Acquire);
                let need_run = !matsuri_bridge::ready()
                    || (cur_turn >= 0
                        && (state != 1
                            || cur_turn != cached_turn
                            || (cur_epoch > 0 && cur_epoch != cached_epoch)));

                // 若同回合刚尝试失败/跳过，1.2 秒内不紧密重试（避免空转）
                let skip_retry = cur_turn >= 0
                    && cur_turn == MT_ATTEMPTED_TURN.load(Acquire)
                    && state != 1
                    && wb_now_ms().saturating_sub(MT_ATTEMPTED_AT.load(Acquire)) < 1200;

                if need_run && !skip_retry {
                    let r = std::panic::catch_unwind(|| unsafe { mt_recompute(cur_turn, cur_epoch) });
                    if r.is_err() {
                        if let Ok(mut g) = MT_ERR.lock() {
                            *g = format!("{} | worker panic", g);
                        }
                        MT_STATE.store(2, std::sync::atomic::Ordering::Release);
                        if cur_turn >= 0 {
                            MT_ATTEMPTED_TURN.store(cur_turn, std::sync::atomic::Ordering::Release);
                            MT_ATTEMPTED_AT.store(wb_now_ms(), std::sync::atomic::Ordering::Release);
                        }
                    }
                    mt_notify_done();
                }

                // 活跃期 200ms 轮询（或被 GetBaseWeight 即时唤醒）
                let (mu, cv) = mt_wake_pair();
                if let Ok(guard) = mu.lock() {
                    if *guard == last_wake_seq {
                        let (g2, _) = cv
                            .wait_timeout(guard, std::time::Duration::from_millis(200))
                            .unwrap_or_else(|e| e.into_inner());
                        last_wake_seq = *g2;
                    } else {
                        last_wake_seq = *guard;
                    }
                }
            }
        });
    if spawned.is_err() {
        MT_WORKER_ON.store(false, Relaxed);
    }
}

unsafe fn mt_recompute(peek_turn: i32, peek_epoch: i32) {
    use std::sync::atomic::Ordering::{Acquire, Release};

    // SIGSEGV 冷却期内不动游戏内存
    let now_s = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    if now_s < SIGSEGV_COOLDOWN_UNTIL.load(Acquire) {
        return;
    }

    ensure_il2cpp_thread_attached();

    // 仅在快速内存读取阶段持有 READ_MUTEX + 线程绑定 sigsetjmp 保护；
    // 拿到纯 Rust 字符串后立刻释锁，避免 MCTS 搜索阻塞 HTTP 端点。
    let (identity, frame) = {
        let _lock = READ_MUTEX.lock().unwrap_or_else(|e| e.into_inner());
        let jmp = sys_sigsetjmp(SIGSEGV_JMP_BUF.as_mut_ptr(), 1);
        if jmp != 0 {
            sigsegv_disarm();
            MT_STATE.store(2, Release);
            mt_set_err("sigsegv_recovered_in_worker".to_string());
            if peek_turn >= 0 {
                MT_ATTEMPTED_TURN.store(peek_turn, Release);
                MT_ATTEMPTED_AT.store(wb_now_ms(), Release);
            }
            return;
        }
        sigsegv_arm();
        let id = mt_read_identity();
        let fr = if id.uma > 0 && id.cards.len() == 6 {
            build_matsuri_frame()
        } else {
            String::new()
        };
        sigsegv_disarm();
        (id, fr)
    };

    if identity.uma == 0 || identity.cards.len() != 6 {
        MT_STATE.store(2, Release);
        mt_set_err(format!(
            "identity_not_ready(uma={}, cards={:?})",
            identity.uma, identity.cards
        ));
        if peek_turn >= 0 {
            MT_ATTEMPTED_TURN.store(peek_turn, Release);
            MT_ATTEMPTED_AT.store(wb_now_ms(), Release);
        }
        return;
    }

    // 身份变了才重建 session（重建会丢会话状态）
    let key = format!("{}:{:?}", identity.uma, identity.cards);
    let need_init = !matsuri_bridge::ready()
        || MT_ID_KEY
            .lock()
            .map(|g| g.as_str() != key.as_str())
            .unwrap_or(true);
    if need_init {
        match matsuri_bridge::init(identity.uma, &identity.cards, 61444) {
            Ok(()) => {
                if let Ok(mut g) = MT_ID_KEY.lock() {
                    *g = key;
                }
            }
            Err(e) => {
                MT_STATE.store(2, Release);
                mt_set_err(format!(
                    "init(uma={}, cards={:?}): {e}",
                    identity.uma, identity.cards
                ));
                if peek_turn >= 0 {
                    MT_ATTEMPTED_TURN.store(peek_turn, Release);
                    MT_ATTEMPTED_AT.store(wb_now_ms(), Release);
                }
                return;
            }
        }
    }

    if frame.is_empty() || frame.contains("\"error\"") {
        MT_STATE.store(2, Release);
        mt_set_err(format!("frame: {}", frame.chars().take(120).collect::<String>()));
        if peek_turn >= 0 {
            MT_ATTEMPTED_TURN.store(peek_turn, Release);
            MT_ATTEMPTED_AT.store(wb_now_ms(), Release);
        }
        return;
    }

    let has_real_festa = frame.contains("festa_dataset_live");
    MT_FESTA_LIVE.store(has_real_festa, Release);

    let decision = match matsuri_bridge::decide(&frame) {
        Ok(d) => d,
        Err(e) => {
            MT_STATE.store(2, Release);
            mt_set_err(format!("decide: {e}"));
            if peek_turn >= 0 {
                MT_ATTEMPTED_TURN.store(peek_turn, Release);
                MT_ATTEMPTED_AT.store(wb_now_ms(), Release);
            }
            return;
        }
    };

    MT_LAST_MS.store(decision.elapsed_ms, Release);
    MT_ATTEMPTED_TURN.store(decision.turn, Release);
    MT_ATTEMPTED_AT.store(wb_now_ms(), Release);

    // 重复帧（同回合幂等 skip）直接保留当前 MT_STATE=1 与 MT_W，并对齐 MT_TURN
    if decision.chosen < 0 || decision.actions.is_empty() {
        if decision.note.contains("重复帧") {
            if MT_STATE.load(Acquire) == 1 && MT_TURN.load(Acquire) == decision.turn {
                if peek_epoch > 0 {
                    MT_EPOCH_ID.store(peek_epoch, Release);
                }
                return;
            }
            return;
        }
        MT_TURN.store(decision.turn, Release);
        if peek_epoch > 0 {
            MT_EPOCH_ID.store(peek_epoch, Release);
        }
        MT_CHOSEN_TAG.store(-1, Release);
        MT_STATE.store(0, Release);
        mt_set_err(format!("skip: {}", decision.note));
        return;
    }

    let has_act_cand = decision
        .actions
        .iter()
        .any(|(tag, _)| matches!(mt_slot(*tag), Some(5..=9)));
    // ★ ci_patch16: 真实 FestaDataSet 就绪时（无论本回合是否有满槽稽古），统一 10 槽统筹并把首选拉到 1000
    let unified_10slot = has_real_festa || has_act_cand;

    let mut min_score = f64::INFINITY;
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
    // 若首选是休息/外出/保健/比赛，unified_10slot 下 next_w[0..10] 全为 0，放行原版休息/外出/保健/比赛

    for i in 0..MT_W.len() {
        MT_W[i].store(next_w[i], Release);
    }

    MT_TURN.store(decision.turn, Release);
    if peek_epoch > 0 {
        MT_EPOCH_ID.store(peek_epoch, Release);
    }
    MT_CHOSEN_TAG.store(decision.chosen_tag, Release);
    MT_STATE.store(1, Release);
    mt_set_err(String::new());
}

/// hook 用：给定 omakase 行号，返回 matsuri 的权重（None = 不接管）
/// ★ ci_patch16: 携带回合号校验 + 即时唤醒有界等待，彻底消除“拿上回合旧权重打这回合”
unsafe fn mt_weight_for_arg(arg: i32) -> Option<i32> {
    use std::sync::atomic::Ordering::{Acquire, Relaxed};
    // 活跃信号：worker 靠这个判断"现在是不是在决策中"
    MT_LAST_HIT.store(wb_now_ms(), Relaxed);

    // 非训练/稽古行直接放行，不触发等待
    let tag = matsuri_bridge::omakase_arg_to_tag(arg)?;
    let slot = mt_slot(tag)?;

    let (cur_turn, cur_epoch) = mt_peek_turn_fast();
    if cur_turn >= 0 {
        let mut turn_ok = MT_STATE.load(Acquire) == 1
            && MT_TURN.load(Acquire) == cur_turn
            && (cur_epoch <= 0 || MT_EPOCH_ID.load(Acquire) == cur_epoch);

        if !turn_ok {
            // 立即唤醒 worker 算本回合
            mt_notify_wake();

            // 仅当引擎已完成 7.3MB 数据初始化且 worker 尚未结算过本回合时，有界等待本回合出分
            let already_attempted = MT_ATTEMPTED_TURN.load(Acquire) == cur_turn
                && wb_now_ms().saturating_sub(MT_ATTEMPTED_AT.load(Acquire)) < 1200;
            if matsuri_bridge::ready() && !already_attempted {
                MT_SYNC_WAITS.fetch_add(1, Relaxed);
                let budget = std::time::Duration::from_millis(matsuri_bridge::wait_budget_ms());
                let deadline = std::time::Instant::now() + budget;
                let (mu, cv) = mt_done_pair();
                if let Ok(mut guard) = mu.lock() {
                    loop {
                        if MT_TURN.load(Acquire) == cur_turn
                            && MT_STATE.load(Acquire) == 1
                            && (cur_epoch <= 0 || MT_EPOCH_ID.load(Acquire) == cur_epoch)
                        {
                            turn_ok = true;
                            break;
                        }
                        if MT_ATTEMPTED_TURN.load(Acquire) == cur_turn
                            && wb_now_ms().saturating_sub(MT_ATTEMPTED_AT.load(Acquire)) < 1200
                        {
                            break;
                        }
                        let now_inst = std::time::Instant::now();
                        if now_inst >= deadline {
                            break;
                        }
                        let rem = deadline - now_inst;
                        let (g2, _) = cv
                            .wait_timeout(guard, rem)
                            .unwrap_or_else(|e| e.into_inner());
                        guard = g2;
                    }
                }
                if turn_ok {
                    MT_SYNC_HITS.fetch_add(1, Relaxed);
                }
            }

            if !turn_ok {
                // 严禁复用上回合旧权重！未对齐时退回原版
                MT_STALE_SKIPS.fetch_add(1, Relaxed);
                return None;
            }
        }
    } else if MT_STATE.load(Acquire) != 1 {
        return None;
    }

    let w = MT_W[slot].load(Acquire);
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
    let cur_turn = MT_PEEKED_TURN.load(Relaxed);
    let cur_epoch = MT_PEEKED_EPOCH.load(Relaxed);
    format!(
        r#"{{"ok":true,"state":{},"ready":{},"festa_live":{},"turn":{},"cur_turn":{},"epoch":{},"cur_epoch":{},"attempted_turn":{},"chosen_tag":{},"hits":{},"sync_waits":{},"sync_hits":{},"stale_skips":{},"last_ms":{},"weights":[{}],"last_error":"{}"}}"#,
        MT_STATE.load(Relaxed),
        matsuri_bridge::ready(),
        MT_FESTA_LIVE.load(Relaxed),
        MT_TURN.load(Relaxed),
        cur_turn,
        MT_EPOCH_ID.load(Relaxed),
        cur_epoch,
        MT_ATTEMPTED_TURN.load(Relaxed),
        MT_CHOSEN_TAG.load(Relaxed),
        MT_HITS.load(Relaxed),
        MT_SYNC_WAITS.load(Relaxed),
        MT_SYNC_HITS.load(Relaxed),
        MT_STALE_SKIPS.load(Relaxed),
        MT_LAST_MS.load(Relaxed),
        parts.join(","),
        err
    )
}'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch16: {msg}")
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
        s = _replace_once(s, FRAME_HEAD_OLD, FRAME_HEAD_NEW, "frame_head_obscured_bool")
        s = _replace_once(s, FRAME_BODY_OLD, FRAME_BODY_NEW, "frame_body_real_festa")
        s = _replace_once(s, ID_SM_OLD, ID_SM_NEW, "identity_direct_sm")
        s = _replace_once(s, WORKER_AND_HOOK_OLD, WORKER_AND_HOOK_NEW, "worker_sync_and_10slot")
    except RuntimeError as e:
        return _fail(str(e))

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    for needle in (
        "fn read_obscured_bool_at",
        "read_ptr_at(chara, 1232)",
        "festa_dataset_live",
        "fn mt_peek_turn_fast",
        "MT_STALE_SKIPS",
        "MT_SYNC_WAITS",
        "unified_10slot",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())
