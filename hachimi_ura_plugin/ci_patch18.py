#!/usr/bin/env python3
"""CI 侧补丁 18：修复故障 #12（补接 baseGame.raceHistory 解除后 50 回合 MCTS 截断）+ T2 教诲弹窗过渡保护。

实测根因与验证（2026-11-28，雷电 14 + 游戏 2.33.2，Run #4 复盘）：
  1. 故障 #12（P0）：
     - 无声铃鹿（100201）等马娘在 umaDB.json 含自选比赛义务区间 freeRaces（如 T12..T26 需赛 1 场）。
     - build_matsuri_frame() 未输出 baseGame.raceHistory（默认空数组 []），导致 T27..T76 共 50 个回合
       MatsuriGame::restored_career_failed(turn) 恒返回 true，全部 128 条 MCTS rollout 在第 1 步即被
       提前终止（耗时从 187~274ms 骤降至 2~4ms，评分从 ~71k 坍缩为当前静态分，出现 0 体力硬点训练）。
     - 实机核验 2.33.2 布局：
         WorkSingleModeData + 208 (0xd0) = _raceHistoryInfoList (List<RaceHistoryInfo>)
         RaceHistoryInfo + 16 (0x10)     = _turn (ObscuredInt, 1-based 1..=78，减 1 转 0-based)
       读取全部已赛回合（含生涯赛与自选赛、含输赢，符合 matsuri online_protocol.md 口径），
       去重升序后输出 "raceHistory":[...]。

  2. T2 教诲附着弹窗过渡保护：
     - 进入 T2 演目教诲弹窗瞬间，5 条演目 adviceId 短暂全为 0，触发 4 次无意义 rollout 报错（failed=4）。
     - 检测 turn >= 2 && !acts.is_empty() && acts_advice_nonzero == 0 时标记 notes="advice_pending"，
       mt_recompute() 跳过本次推演且不置 attempted 锁，待弹窗确认后 200ms 内自动完成正常搜索。
"""

import sys

PATH = "src/lib.rs"

# 1) 在 build_matsuri_frame() 中读取 _raceHistoryInfoList (+208) 并记录 acts_advice_nonzero
RACE_AND_ACTS_OLD = r'''    // ---- 解析真实 WorkSingleModeScenarioFestaDataSet（若存在）----
    let mut camp_remain: i32 = 0;
    let mut max_act_level: i32 = 10;
    let mut acts: Vec<String> = Vec::new();
    let mut festa_persons: Vec<String> = Vec::new();
    let mut check_points: Vec<String> = Vec::new();
    let mut kensan = [false; 5];
    let has_dataset = is_valid_user_ptr(dataset_obj);'''

RACE_AND_ACTS_NEW = r'''    // ★ ci_patch18: 读取 WorkSingleModeData+208 (_raceHistoryInfoList: List<RaceHistoryInfo>)
    //   RaceHistoryInfo+16 = _turn (ObscuredInt, 1-based 1..=78 -> 0-based 0..=77)
    //   修复故障 #12：补齐 baseGame.raceHistory，防止自选赛窗口过期后 career_failed=true 截断后半程 MCTS
    let mut race_history: Vec<i32> = Vec::new();
    let rh_list = read_ptr_at(sm, 208);
    if is_valid_user_ptr(rh_list) {
        let rh_cnt = read_int_at(rh_list, 0x18);
        let rh_items = read_ptr_at(rh_list, 0x10);
        if rh_cnt > 0 && rh_cnt < 128 && is_valid_user_ptr(rh_items) {
            let base = rh_items as *const u8;
            for i in 0..(rh_cnt as usize) {
                let item = std::ptr::read_unaligned::<*mut c_void>(
                    base.add(IL2CPP_LIST_ITEMS_OFF + i * IL2CPP_LIST_ITEM_SIZE)
                        as *const *mut c_void,
                );
                if !is_valid_user_ptr(item) {
                    continue;
                }
                let raw_rh_turn = read_obscured_int_at(item as *const c_void, 16);
                if (1..=78).contains(&raw_rh_turn) {
                    let t0 = raw_rh_turn - 1;
                    if !race_history.contains(&t0) {
                        race_history.push(t0);
                    }
                }
            }
        }
    }
    race_history.sort_unstable();
    let race_history_json = race_history
        .iter()
        .map(|t| t.to_string())
        .collect::<Vec<_>>()
        .join(",");

    // ---- 解析真实 WorkSingleModeScenarioFestaDataSet（若存在）----
    let mut camp_remain: i32 = 0;
    let mut max_act_level: i32 = 10;
    let mut acts: Vec<String> = Vec::new();
    let mut acts_advice_nonzero: usize = 0;
    let mut festa_persons: Vec<String> = Vec::new();
    let mut check_points: Vec<String> = Vec::new();
    let mut kensan = [false; 5];
    let has_dataset = is_valid_user_ptr(dataset_obj);'''

# 2) 统计 acts_advice_nonzero
ADVICE_COUNT_OLD = r'''                    let advice_id = read_obscured_int_at(e, 116);
                    let fusion_id = *fusion_map.get(&advice_id).unwrap_or(&0);'''

ADVICE_COUNT_NEW = r'''                    let advice_id = read_obscured_int_at(e, 116);
                    if advice_id > 0 {
                        acts_advice_nonzero += 1;
                    }
                    let fusion_id = *fusion_map.get(&advice_id).unwrap_or(&0);'''

# 3) notes_json 标记 advice_pending，并在 baseGame JSON 中输出 raceHistory
FRAME_FMT_OLD = r'''    let notes_json = if has_dataset && !acts.is_empty() {
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
    )'''

FRAME_FMT_NEW = r'''    let advice_pending = has_dataset && !acts.is_empty() && turn >= 2 && acts_advice_nonzero == 0;
    let notes_json = if advice_pending {
        r#"["festa_dataset_live","advice_pending"]"#
    } else if has_dataset && !acts.is_empty() {
        r#"["festa_dataset_live"]"#
    } else {
        r#"["dataset_missing"]"#
    };

    format!(
        r#"{{"festa":{{"acts":[{}],"maxActLevel":{},"campRemain":{},"assistSupportLeft":{},"fatigueRecovery":{},"trainLevelTotal":{},"kensan":[{},{},{},{},{}],"persons":[{}],"checkPoints":[{}]}},"baseGame":{{"scenarioId":{},"umaId":{},"turn":{},"vital":{},"maxVital":{},"motivation":{},"fiveStatus":[{},{},{},{},{}],"fiveStatusLimit":[{},{},{},{},{}],"skillPt":{},"skillScore":0,"totalHints":0,"trainLevelCount":[{},{},{},{},{}],"ptScoreRate":1.0,"failureRateBias":0,"isIll":{},"isQieZhe":false,"isXiaoQie":false,"isAiJiao":false,"PositiveThinkingCount":0,"isRefreshMind":false,"LuckyCount":0,"zhongMaBlueCount":[0,0,0,0,0],"isRacing":false,"cardId":[{}],"persons":[{}],"personDistribution":[{}],"lockedTrainingId":-1,"friendship_noncard_yayoi":{},"friendship_noncard_reporter":{},"friend_stage":{},"friend_outgoingUsed":{},"playing_state":1,"source":"command","isAfterTraining":false,"raceHistory":[{}]{},"charaEffects":[{}],"training":{{}}}},"notes":{}}}"#,
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
        race_history_json,
        epoch_field,
        eff_json,
        notes_json
    )'''

# 4) mt_recompute() 遇到 advice_pending 过渡帧直接跳过（不计入 failed，也不加 1.2s attempted 退避）
ADVICE_GUARD_OLD = r'''    let has_real_festa = frame.contains("festa_dataset_live");
    MT_FESTA_LIVE.store(has_real_festa, Release);

    let decision = match matsuri_bridge::decide(&frame) {'''

ADVICE_GUARD_NEW = r'''    let has_real_festa = frame.contains("festa_dataset_live");
    MT_FESTA_LIVE.store(has_real_festa, Release);

    // ★ ci_patch18: 教诲附着弹窗过渡帧（turn>=2 且全部 adviceId==0），跳过本次推演待确认后再算
    if frame.contains("advice_pending") {
        MT_STATE.store(0, Release);
        mt_set_err("skip: advice_pending".to_string());
        return;
    }

    let decision = match matsuri_bridge::decide(&frame) {'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch18: {msg}")
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
        s = _replace_once(s, RACE_AND_ACTS_OLD, RACE_AND_ACTS_NEW, "race_history_read")
        s = _replace_once(s, ADVICE_COUNT_OLD, ADVICE_COUNT_NEW, "advice_nonzero_count")
        s = _replace_once(s, FRAME_FMT_OLD, FRAME_FMT_NEW, "frame_fmt_race_history")
        s = _replace_once(s, ADVICE_GUARD_OLD, ADVICE_GUARD_NEW, "advice_pending_guard")
    except RuntimeError as e:
        return _fail(str(e))

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    for needle in (
        "read_ptr_at(sm, 208)",
        "raceHistory",
        "advice_pending",
        "acts_advice_nonzero",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())
