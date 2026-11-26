#!/usr/bin/env python3
"""CI 侧补丁：把 matsuri 完整决策栈挂进 lib.rs。

为什么在 CI 里改而不是直接改 lib.rs：
  lib.rs 约 25k 行 / 1MB，手工整文件替换不可靠；脚本化替换每处都校验锚点命中数，
  锚点对不上就非零退出，比"悄悄没挂上"安全。

本脚本做四件事：
  1. `mod matsuri_bridge;`（cfg(feature="matsuri")）
  2. 注入 `build_matsuri_frame()`：从游戏内存直接读全字段，拼 matsuri 在线帧
     （thisTurn.json 形状：festa + baseGame + baseGame.training）
  3. ENDPOINTS 注册 /api/ai/{status,init,frame,decide}
  4. 路由分支

为什么必须挂 /api/ai/decide：
  `matsuri_bridge::decide` 没有调用者时，LTO 会把整条决策链
  （ParsedFrame::parse → MatsuriGame::restore → route → Trainer → value 打分）
  当死代码删掉，产物里只剩数据表和 OnlineSession::new 的构造路径。
  实测判据：strip 后 `handle_frame` / `bridge_not_initialized` 这些串消失。
  所以四个端点一个都不能省。

锚点清单（改 lib.rs 结构时必须同步）：
  1. `mod ui_rename;`                       模块声明区
  2. `    "/scenario",`                     const ENDPOINTS 首条
  3. `    } else if path == "/scenario" {`  请求路由链
  4. `unsafe fn read_summary_inner() -> String {`  注入帧构造函数的落点
"""

import sys

PATH = "src/lib.rs"

FRAME_FN = r'''
// ===== matsuri 在线帧构造 =====
//
// 输出形状 = 引擎 thisTurn.json：{ "festa": {...}, "baseGame": {...} }
// 全部走直接内存读（read_obscured_int_at / read_ptr_at / seq_*），
// 不新增 il2cpp_runtime_invoke（read_summary 的安全阈值 ~130 次，不能加）。
//
// 数组约定（本仓实测）：+0x18 长度、+0x20 元素区、元素 8 字节指针
// ObscuredInt：+off = key、+off+4 = hidden，真值 = hidden ^ key
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
    let sm = call_getter_ref(wdm_class, wdm, "get_SingleMode");
    if sm.is_null() {
        return r#"{"error":"no_single_mode"}"#.to_string();
    }

    // _totalTurnNum(68)：>0 才在育成局内；游戏值 1 基，帧要 0 基
    let raw_turn = read_obscured_int_at(sm, 68);
    if raw_turn <= 0 {
        return r#"{"error":"not_in_training"}"#.to_string();
    }

    let chara_class = find_class(
        image,
        to_cstr("Gallop").as_ptr(),
        to_cstr("WorkSingleModeCharaData").as_ptr(),
    );
    let chara = call_getter_ref(sm_class, sm, "get_Character");
    if chara.is_null() {
        return r#"{"error":"no_chara"}"#.to_string();
    }

    // ---- baseGame：马娘与五维 ----
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
    let eff_ids: Vec<i32> = if eff_arr.is_null() {
        Vec::new()
    } else {
        read_il2cpp_int_list(eff_arr)
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
    if dataset_obj.is_null() {
        // 数据集没绑定 = 不在决策点（结算中间态 / 未开场），直接报错而不是喂残帧
        return format!(
            r#"{{"error":"dataset_null","scenario_id":{},"turn":{}}}"#,
            sid, turn
        );
    }

    // FestaInfo(+16)：+16 合宿剩余、+36 演目上限
    let fi = read_ptr_at(dataset_obj, 16);
    let (camp_remain, max_act_level) = if fi.is_null() {
        (0, 10)
    } else {
        (
            read_obscured_int_at(fi, 16),
            read_obscured_int_at(fi, 36),
        )
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
}
'''

ROUTES = r'''    } else if path == "/api/ai/status" {
        #[cfg(feature = "matsuri")]
        {
            matsuri_bridge::status_json()
        }
        #[cfg(not(feature = "matsuri"))]
        {
            r#"{"ok":false,"error":"matsuri_feature_off"}"#.to_string()
        }
    } else if path == "/api/ai/init" {
        #[cfg(feature = "matsuri")]
        {
            // 配置：/sdcard/Download/uma_so/matsuri_uma.txt
            //   uma=101301
            //   cards=303174,303114,303004,303224,303204,303234
            //   seed=61444
            let mut uma = 0u32;
            let mut seed = 61444u64;
            let mut cards: Vec<u32> = Vec::new();
            if let Ok(text) = std::fs::read_to_string("/sdcard/Download/uma_so/matsuri_uma.txt") {
                for line in text.lines() {
                    let line = line.trim();
                    if line.is_empty() || line.starts_with('#') {
                        continue;
                    }
                    if let Some(v) = line.strip_prefix("uma=") {
                        uma = v.trim().parse().unwrap_or(0);
                    } else if let Some(v) = line.strip_prefix("seed=") {
                        seed = v.trim().parse().unwrap_or(61444);
                    } else if let Some(v) = line.strip_prefix("cards=") {
                        cards = v
                            .split(',')
                            .filter_map(|x| x.trim().parse::<u32>().ok())
                            .collect();
                    }
                }
            }
            match matsuri_bridge::init(uma, &cards, seed) {
                Ok(()) => format!(
                    r#"{{"ok":true,"uma":{},"cards":{},"seed":{}}}"#,
                    uma,
                    cards.len(),
                    seed
                ),
                Err(e) => format!(r#"{{"ok":false,"error":"{}"}}"#, e.replace('"', "'")),
            }
        }
        #[cfg(not(feature = "matsuri"))]
        {
            r#"{"ok":false,"error":"matsuri_feature_off"}"#.to_string()
        }
    } else if path == "/api/ai/frame" {
        #[cfg(feature = "matsuri")]
        {
            unsafe { build_matsuri_frame() }
        }
        #[cfg(not(feature = "matsuri"))]
        {
            r#"{"ok":false,"error":"matsuri_feature_off"}"#.to_string()
        }
    } else if path == "/api/ai/decide" {
        #[cfg(feature = "matsuri")]
        {
            let frame = unsafe { build_matsuri_frame() };
            if frame.contains("\"error\"") {
                frame
            } else {
                let t0 = std::time::Instant::now();
                match matsuri_bridge::decide(&frame) {
                    Ok(d) => {
                        let scores = d
                            .scores
                            .iter()
                            .map(|(i, v)| format!(r#"{{"i":{},"v":{:.3}}}"#, i, v))
                            .collect::<Vec<_>>()
                            .join(",");
                        format!(
                            r#"{{"ok":true,"turn":{},"epoch":{},"kind":"{}","chosen":{},"desc":"{}","note":"{}","candidates":{},"search_backed":{},"scores":[{}],"elapsed_ms":{}}}"#,
                            d.turn,
                            d.epoch,
                            d.kind,
                            d.chosen,
                            d.action_desc.replace('"', "'"),
                            d.note.replace('"', "'"),
                            d.candidate_n,
                            d.search_backed,
                            scores,
                            t0.elapsed().as_millis()
                        )
                    }
                    Err(e) => format!(r#"{{"ok":false,"error":"{}"}}"#, e.replace('"', "'")),
                }
            }
        }
        #[cfg(not(feature = "matsuri"))]
        {
            r#"{"ok":false,"error":"matsuri_feature_off"}"#.to_string()
        }
'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch: {msg}")
    return 1


def main() -> int:
    s = open(PATH, encoding="utf-8").read()
    before = len(s)

    # ---- 1) 模块声明 ----
    anchor = "mod ui_rename;"
    if s.count(anchor) != 1:
        return _fail(f"anchor 'mod ui_rename;' count={s.count(anchor)}")
    s = s.replace(
        anchor,
        anchor + '\n#[cfg(feature = "matsuri")]\nmod matsuri_bridge;',
        1,
    )

    # ---- 2) 注入帧构造函数（放在 read_summary_inner 之前）----
    anchor = "unsafe fn read_summary_inner() -> String {"
    if s.count(anchor) != 1:
        return _fail(f"anchor read_summary_inner count={s.count(anchor)}")
    s = s.replace(anchor, FRAME_FN.strip() + "\n\n" + anchor, 1)

    # ---- 3) ENDPOINTS 注册 ----
    anchor = '    "/scenario",'
    if s.count(anchor) < 1:
        return _fail("anchor ENDPOINTS '\"/scenario\",' missing")
    s = s.replace(
        anchor,
        anchor
        + '\n    "/api/ai/status",\n    "/api/ai/init",'
          '\n    "/api/ai/frame",\n    "/api/ai/decide",',
        1,
    )

    # ---- 4) 路由分支 ----
    anchor = '    } else if path == "/scenario" {'
    if s.count(anchor) != 1:
        return _fail(f"route anchor count={s.count(anchor)}")
    s = s.replace(anchor, ROUTES + anchor, 1)

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    for needle in (
        "mod matsuri_bridge",
        "fn build_matsuri_frame",
        "/api/ai/frame",
        "/api/ai/decide",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())