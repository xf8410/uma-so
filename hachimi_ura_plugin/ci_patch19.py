#!/usr/bin/env python3
"""CI 侧补丁 19：骏大祭教诲附着 + 教诲転成 + 全事件选项（含原生 ChoiceReward）三合一 AI 接管 + 热开关。

实机 2.33.2 IL2CPP & master.mdb 逆向核验（2026-11-28，雷电 14）：
  0. 精准方法入口寻址 + 热开关：
     - 新增 `find_method_entry_exact(class, name, param_count)`：同时核对 `il2cpp_method_get_param_count`
       并直读 `MethodInfo + 0` (`methodPointer`)，避免 `find_method_addr` 在 2.33.2 偏移偏差以及
       `UpdateAttachAdviceQuick`（2 参实例 / 3 参静态）重载错配。
     - `mt_weight_for_arg` 与 3 个新 hook 均支持 `matsuri_bridge::is_enabled()` 热开关：
       在 `ai_search.txt` 写入 `enabled=false` 即可秒级回退 100% 纯原版 omakase，无需换包或重启。

  1. 教诲附着（AttachAdviceAutoPlayProxy.AutoAttachAdvice，0 参数）：
     - 嵌套类：`Gallop.SingleModeScenarioFestaAttachAdviceViewController/AttachAdviceAutoPlayProxy`
     - 字段链路：`proxy+16 (_controller)` -> `controller+96 (_viewModel)` -> `vm+48 (<AttachAdviceEntity>)` -> `entity+16 (<OwnedAdviceEntityList>)`
     - 每个 `AdviceEntity`：`+16 <MainAdviceVO>`、`+24 <FusionAdviceVO>`；每个 `AdviceVO`：`+16 <AdviceId>` (plain i32)
     - 写入 API（官方 ViewModel/ViewController 原生方法，零 UI 模拟）：
         `vm.DetachAllAdvice()` (0 params)
         `vm.UpdateAttachAdviceQuick(actType: 1..=5, adviceEntity)` (2 params, instance)
         `controller.UpdateAttachAdviceView()` (0 params)
     - 先调 `original(proxy)` 保底，再调用 `matsuri_bridge::decide_advice(&frame_special)`（<3ms，枚举 120 排列），
       5 个槽位全部精确匹配且唯一时才覆盖写入，否则无缝回退原版。

  2. 教诲転成（FusionAdviceAutoPlayProxy.AutoFusionAdvice，0 参数）：
     - 嵌套类：`Gallop.SingleModeScenarioFestaFusionAdviceViewController/FusionAdviceAutoPlayProxy`
     - 字段链路：`proxy+16 (_viewController)` -> `controller+48 (_viewModel)`
       * `vm+32 <FusionTargetAdviceEntityList>`（本窗口可选転成目标 601/701 或 801/901/1001）
       * `vm+40 <FusionBaseAdviceEntityList>`（未転成基础教诲 101..501）
       * `vm+48 <MasterAdviceVoList>`（全量 AdviceVO 兜底表）
     - 写入 API：
         `vm.ResetAllFusionTargetAdviceVoList()` (0 params)
         `vm.UpdateFusionTargetAdviceVoList(baseVo, targetVo)` (2 params)
         `controller.UpdateOnChangeFusionAdvice()` (0 params)
     - 按 `matsuri_bridge::fusion_pairs(is_year3)`（按卡组构成自动匹配 default/stamina/wisdom/guts 档）
       精确配对覆写，任一目标找不到则安全保留原版。

  3. 全事件选项接管（StoryViewProxy.TryChoice(index: i32) -> bool，1 参数）：
     - 嵌套类：`Gallop.StoryViewController/StoryViewProxy`（仅 omakase 自动选择调用，绝不干扰手动点击！）
     - 读取 `WorkSingleModeData + 440 (0x1b8)` `EventChoiceRewardDict`（`Dictionary<ObscuredInt, EventChoiceReward>`，
       Entry 步长 0x28：`+0x00 hashCode, +0x08 ObscuredInt key, +0x20 EventChoiceReward* value`），
       经 `EventChoiceReward+16 (_branchRewardArray)` -> `EventChoiceBranchReward+16 (_gainParamArray)` ->
       `EventChoiceRewardGainParam (+16 _displayId, +36 _effectValue0, +56 _effectValue1, +76 _effectValue2)`，
       结合 ARM64 反汇编验证的 `GainParameterType`（`1..5` 五维、`10` 体力、`11` 体力上限、`20` 干劲、`30` PT、`51..55` 上限）
       及 `master.mdb` `text_data(cat=394)` 全部 61 条 `displayId` 规则，实时拼装 `baseGame.story`（含成功/失败双分支），
       喂给 `matsuri_bridge::decide_event(&frame)`（<1ms）：
       * 骏大祭特殊事件（如 `400015106` 追加の稽古）优先走 `matsuri` 本地精确规则；
       * 全部马娘/支援卡/新年/合宿多选事件均由 `baseGame.story` 实时喂入 `matsuri` 动态估值器择优；
       * 若字典未缓存或遇未知结构 → 无痕回退原版 omakase `index`，100% 零风险。

  总 Hook 数：6 -> 9（远低于 Dobby 4KB 页上限），三个入口均在低频弹窗触发，不在高频渲染热路径上。
"""

import sys

PATH = "src/lib.rs"

# 1) 扩展 build_matsuri_frame -> build_matsuri_frame_ex，支持 source / playing_state / turn_override / story_id / story_json
FRAME_SIG_OLD = r'''unsafe fn build_matsuri_frame() -> String {'''

FRAME_SIG_NEW = r'''unsafe fn build_matsuri_frame() -> String {
    build_matsuri_frame_ex("command", 1, None, None, None)
}

unsafe fn build_matsuri_frame_ex(
    source_override: &str,
    playing_state_override: i32,
    turn_override: Option<i32>,
    story_id_override: Option<i32>,
    story_json_override: Option<&str>,
) -> String {'''

FRAME_TURN_OLD = r'''    let motivation = read_obscured_int_at(chara, 1056);
    let turn = raw_turn - 1;'''

FRAME_TURN_NEW = r'''    let motivation = read_obscured_int_at(chara, 1056);
    let turn = turn_override.unwrap_or(raw_turn - 1);'''

FRAME_JSON_TAIL_OLD = r'''    format!(
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

FRAME_JSON_TAIL_NEW = r'''    let story_id_field = match story_id_override {
        Some(st_id) if st_id > 0 => format!(r#","storyId":{}"#, st_id),
        _ => String::new(),
    };
    let story_obj_field = match story_json_override {
        Some(sj) if !sj.is_empty() => format!(r#","story":{}"#, sj),
        _ => String::new(),
    };

    format!(
        r#"{{"festa":{{"acts":[{}],"maxActLevel":{},"campRemain":{},"assistSupportLeft":{},"fatigueRecovery":{},"trainLevelTotal":{},"kensan":[{},{},{},{},{}],"persons":[{}],"checkPoints":[{}]}},"baseGame":{{"scenarioId":{},"umaId":{},"turn":{},"vital":{},"maxVital":{},"motivation":{},"fiveStatus":[{},{},{},{},{}],"fiveStatusLimit":[{},{},{},{},{}],"skillPt":{},"skillScore":0,"totalHints":0,"trainLevelCount":[{},{},{},{},{}],"ptScoreRate":1.0,"failureRateBias":0,"isIll":{},"isQieZhe":false,"isXiaoQie":false,"isAiJiao":false,"PositiveThinkingCount":0,"isRefreshMind":false,"LuckyCount":0,"zhongMaBlueCount":[0,0,0,0,0],"isRacing":false,"cardId":[{}],"persons":[{}],"personDistribution":[{}],"lockedTrainingId":-1,"friendship_noncard_yayoi":{},"friendship_noncard_reporter":{},"friend_stage":{},"friend_outgoingUsed":{},"playing_state":{},"source":"{}","isAfterTraining":false,"raceHistory":[{}]{}{}{},"charaEffects":[{}],"training":{{}}}},"notes":{}}}"#,
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
        playing_state_override,
        source_override,
        race_history_json,
        epoch_field,
        story_id_field,
        story_obj_field,
        eff_json,
        notes_json
    )'''

# 2) mt_weight_for_arg 入口接入热开关（enabled=false 时 100% 透传原版 omakase，零开销）
WEIGHT_TOGGLE_OLD = r'''unsafe fn mt_weight_for_arg(arg: i32) -> Option<i32> {
    use std::sync::atomic::Ordering::{Acquire, Relaxed};
    // 活跃信号：worker 靠这个判断"现在是不是在决策中"
    MT_LAST_HIT.store(wb_now_ms(), Relaxed);'''

WEIGHT_TOGGLE_NEW = r'''unsafe fn mt_weight_for_arg(arg: i32) -> Option<i32> {
    use std::sync::atomic::Ordering::{Acquire, Relaxed};
    // ★ ci_patch19: 热开关（ai_search.txt 写 enabled=false 立即全退回原版 omakase，无需重启）
    if !matsuri_bridge::is_enabled() {
        return None;
    }
    // 活跃信号：worker 靠这个判断"现在是不是在决策中"
    MT_LAST_HIT.store(wb_now_ms(), Relaxed);'''

# 3) 在 mt_json 之后追加教诲附着 / 教诲転成 / 全事件选项三合一接管实现
HOOK_IMPL_ANCHOR = r'''    format!(
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

HOOK_IMPL_ADD = HOOK_IMPL_ANCHOR + r'''

// =========================================================================
// ★ ci_patch19: 骏大祭教诲附着 + 教诲転成 + 全事件选项（原生 ChoiceReward）三合一接管
// =========================================================================
static FESTA_ADVICE_HOOKS_INSTALLED: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);
static mut ORIG_ATTACH_ADVICE_PROLOGUE: [u8; 16] = [0; 16];
static mut ORIG_FUSION_ADVICE_PROLOGUE: [u8; 16] = [0; 16];
static mut ORIG_STORY_TRY_CHOICE_PROLOGUE: [u8; 16] = [0; 16];

// 解析缓存：官方原版 ViewModel / ViewController / Proxy 实例方法指针（按精确参数个数从 MethodInfo+0 读取）
static VM_ATTACH_DETACH_ALL_ADDR: std::sync::atomic::AtomicUsize =
    std::sync::atomic::AtomicUsize::new(0);
static VM_ATTACH_QUICK_ADDR: std::sync::atomic::AtomicUsize =
    std::sync::atomic::AtomicUsize::new(0);
static VC_ATTACH_UPDATE_VIEW_ADDR: std::sync::atomic::AtomicUsize =
    std::sync::atomic::AtomicUsize::new(0);
static VM_FUSION_RESET_ADDR: std::sync::atomic::AtomicUsize =
    std::sync::atomic::AtomicUsize::new(0);
static VM_FUSION_UPDATE_ADDR: std::sync::atomic::AtomicUsize =
    std::sync::atomic::AtomicUsize::new(0);
static VC_FUSION_UPDATE_VIEW_ADDR: std::sync::atomic::AtomicUsize =
    std::sync::atomic::AtomicUsize::new(0);
static PROXY_GET_CHOICE_NUM_ADDR: std::sync::atomic::AtomicUsize =
    std::sync::atomic::AtomicUsize::new(0);

static ADVICE_OVERRIDE_HITS: std::sync::atomic::AtomicU64 =
    std::sync::atomic::AtomicU64::new(0);
static ADVICE_LAST_EPOCH: std::sync::atomic::AtomicI32 =
    std::sync::atomic::AtomicI32::new(-1);
static ADVICE_LAST_WINDOW: std::sync::atomic::AtomicI32 =
    std::sync::atomic::AtomicI32::new(-1);
static FUSION_OVERRIDE_HITS: std::sync::atomic::AtomicU64 =
    std::sync::atomic::AtomicU64::new(0);
static EVENT_OVERRIDE_HITS: std::sync::atomic::AtomicU64 =
    std::sync::atomic::AtomicU64::new(0);

/// ★ ci_patch19: 精确按方法名 + 参数个数从 MethodInfo+0 (methodPointer) 解析入口地址（与 /il2cpp/disassemble 同源）
unsafe fn find_method_entry_exact(class: *mut c_void, method_name: &str, param_count: i32) -> usize {
    if class.is_null() {
        return 0;
    }
    let get_methods_p = resolve_il2cpp_symbol("il2cpp_class_get_methods");
    let get_name_p = resolve_il2cpp_symbol("il2cpp_method_get_name");
    let get_pcnt_p = resolve_il2cpp_symbol("il2cpp_method_get_param_count");
    if get_methods_p.is_null() || get_name_p.is_null() {
        return 0;
    }
    let get_methods: unsafe extern "C" fn(*mut c_void, *mut *mut c_void) -> *const c_void =
        std::mem::transmute(get_methods_p);
    let get_name: unsafe extern "C" fn(*const c_void) -> *const c_char =
        std::mem::transmute(get_name_p);
    let get_pcnt: Option<unsafe extern "C" fn(*const c_void) -> u32> = if get_pcnt_p.is_null() {
        None
    } else {
        Some(std::mem::transmute(get_pcnt_p))
    };
    let mut iter: *mut c_void = std::ptr::null_mut();
    let mut fallback: usize = 0;
    loop {
        let mi = get_methods(class, &mut iter);
        if mi.is_null() {
            break;
        }
        let np = get_name(mi);
        if np.is_null() {
            continue;
        }
        if CStr::from_ptr(np).to_string_lossy() != method_name {
            continue;
        }
        let ptr = std::ptr::read_unaligned::<*const c_void>(mi as *const *const c_void) as usize;
        if ptr == 0 {
            continue;
        }
        if param_count < 0 {
            return ptr;
        }
        if let Some(f) = get_pcnt {
            if f(mi) as i32 == param_count {
                return ptr;
            }
        } else if fallback == 0 {
            fallback = ptr;
        }
    }
    fallback
}

/// 通用安全读取 IL2CPP 引用序列（同时兼容 T[]、List<T>、ReadOnlyCollection<T>）
unsafe fn read_il2cpp_ref_seq(obj: *const c_void, max_n: usize) -> Vec<*mut c_void> {
    let mut out = Vec::new();
    if !is_valid_user_ptr(obj) {
        return out;
    }
    let mut cur = obj;
    for _ in 0..2 {
        let klass = std::ptr::read_unaligned::<*mut c_void>(cur as *const *mut c_void);
        if !is_valid_user_ptr(klass) {
            return out;
        }
        let cname = get_class_name_from_pointer(klass);
        if cname.contains("ReadOnlyCollection") {
            let inner = read_ptr_at(cur, 0x10);
            if !is_valid_user_ptr(inner) {
                return out;
            }
            cur = inner;
            continue;
        }
        if cname.contains("List") {
            let items = read_ptr_at(cur, 0x10);
            let cnt = read_int_at(cur, 0x18);
            if cnt <= 0 || cnt > (max_n as i32) || !is_valid_user_ptr(items) {
                return out;
            }
            let base = items as *const u8;
            for i in 0..(cnt as usize) {
                let p = std::ptr::read_unaligned::<*mut c_void>(
                    base.add(IL2CPP_LIST_ITEMS_OFF + i * IL2CPP_LIST_ITEM_SIZE)
                        as *const *mut c_void,
                );
                if is_valid_user_ptr(p) {
                    out.push(p);
                }
            }
            return out;
        }
        let n = seq_len(cur);
        if n == 0 || n > max_n {
            return out;
        }
        for i in 0..n {
            let p = seq_item(cur, i);
            if is_valid_user_ptr(p) {
                out.push(p);
            }
        }
        return out;
    }
    out
}

/// 将实机回合号贴合到最近的 matsuri 教诲窗口（[2, 11, 23, 35, 47, 59]）
fn snap_to_advice_window(turn: i32) -> i32 {
    const WINDOWS: [i32; 6] = [2, 11, 23, 35, 47, 59];
    let mut best = WINDOWS[0];
    let mut best_diff = (turn - best).abs();
    for &w in &WINDOWS[1..] {
        let d = (turn - w).abs();
        if d < best_diff {
            best = w;
            best_diff = d;
        }
    }
    best
}

/// 从 EventChoiceBranchReward 解码 10 槽 StoryEffectValue: [spd, sta, pow, guts, wiz, pt, hint_lv, vital, bond, mot]
/// 依据 2.33.2 ARM64 反汇编 (GainParameterType) + master.mdb text_data(cat=394) 全部 61 条 displayId 规则
unsafe fn decode_choice_branch_values(
    branch_obj: *const c_void,
    cur_stats: [i32; 5],
    is_ill: bool,
) -> Option<[i32; 10]> {
    if !is_valid_user_ptr(branch_obj) {
        return None;
    }
    let gain_arr = read_ptr_at(branch_obj, 16); // EventChoiceBranchReward._gainParamArray
    if !is_valid_user_ptr(gain_arr) {
        return None;
    }
    let n = seq_len(gain_arr);
    if n == 0 || n > 32 {
        return None;
    }
    let mut v = [0i32; 10]; // [0..5]=五维, [5]=PT, [6]=HintLv, [7]=Vital, [8]=Bond, [9]=Motivation
    let mut recognized = false;
    for i in 0..n {
        let gp = seq_item(gain_arr, i);
        if !is_valid_user_ptr(gp) {
            continue;
        }
        let disp_id = read_obscured_int_at(gp, 16); // _displayId
        let e0 = read_obscured_int_at(gp, 36);      // _effectValue0
        let e1 = read_obscured_int_at(gp, 56);      // _effectValue1
        let e2 = read_obscured_int_at(gp, 76);      // _effectValue2
        if disp_id <= 0 || disp_id > 200 {
            continue;
        }
        recognized = true;
        match disp_id {
            // id=1: {param} +{val} ; id=2: {param} -{val}
            1 | 2 => {
                let sign = if disp_id == 1 { 1 } else { -1 };
                let delta = sign * e1;
                match e0 {
                    1..=5 => v[(e0 - 1) as usize] += delta, // Speed..Wiz
                    10 => v[7] += delta,                    // Hp (Vital)
                    11 => v[7] += delta * 2,                // MaxHp (高价值体力上限折算)
                    20 => v[9] += delta,                    // Motivation
                    30 => v[5] += delta,                    // SkillPoint
                    51..=55 => v[(e0 - 51) as usize] += delta / 2, // MaxSpeed..MaxWiz
                    _ => {}
                }
            }
            // id=4/5: 羁绊 +e1 ; id=35/36: 羁绊 -e1
            4 | 5 => v[8] += e1,
            35 | 36 => v[8] -= e1,
            // id=27/28/29/30: 低羁绊支援卡加羁绊
            27 => v[8] += e1.max(1) * e2,
            28 => v[8] += e0.max(1) * e1,
            29 => v[8] += e1,
            30 => v[8] += e0,
            // id=6/58: 技能灵感 Lv +e1 ; id=7: 直接获得技能
            6 | 58 => v[6] += e1.max(1),
            7 => v[6] += 5,
            // id=8: 概率随机技能
            8 => v[6] += 2,
            // id=9/37: 获得状态 e0（1..=6 负面病种，7..=10 爱娇/练习上手/注目株/切者）
            9 | 37 => match e0 {
                1..=6 => v[5] -= 90,   // 负面病种重罚
                10 => v[5] += 220,     // 切れ者（极高价值）
                7 | 8 | 9 => v[5] += 80, // 愛嬌 / 練習上手 / 注目株
                _ => {}
            },
            // id=10/15/16: 治愈坏状态
            10 | 15 | 16 => {
                if is_ill {
                    v[5] += 80;
                }
            }
            // id=11: 友人解锁外出
            11 => v[5] += 120,
            // id=13: 全五维 +e0
            13 => {
                for s in 0..5 {
                    v[s] += e0;
                }
            }
            // id=14/34: 随机 e0 项五维 ±e1（均摊至 5 维做期望）
            14 | 34 => {
                let sign = if disp_id == 14 { 1 } else { -1 };
                let per = (sign * e0.clamp(1, 5) * e1) / 5;
                for s in 0..5 {
                    v[s] += per;
                }
            }
            // id=19/20: 上次训练对应属性 ±e0（近似均摊）
            19 | 20 => {
                let sign = if disp_id == 19 { 1 } else { -1 };
                let per = (sign * e0) / 5;
                for s in 0..5 {
                    v[s] += per;
                }
            }
            // id=55: SkillPt -e0
            55 => v[5] -= e0,
            // id=56: 最高属性 +e0 ; id=57: 最低属性 +e0
            56 => {
                let mut best_i = 0usize;
                for s in 1..5 {
                    if cur_stats[s] > cur_stats[best_i] {
                        best_i = s;
                    }
                }
                v[best_i] += e0;
            }
            57 => {
                let mut min_i = 0usize;
                for s in 1..5 {
                    if cur_stats[s] < cur_stats[min_i] {
                        min_i = s;
                    }
                }
                v[min_i] += e0;
            }
            // id=61: 骏大祭最低演目 Lv +1 ; id=63: 最低演目槽 +1
            61 => v[5] += 110,
            63 => v[5] += 40,
            // id=38/39/40/41/42/43: 被超绝好调/护盾抵消的效果，不计减益
            _ => {}
        }
    }
    if recognized {
        Some(v)
    } else {
        None
    }
}

/// 从 WorkSingleModeData+440 (EventChoiceRewardDict) 构建当前弹窗的 baseGame.story JSON
/// 必须已在 READ_MUTEX + sigsetjmp 保护下调用！
unsafe fn build_live_story_json(story_id: i32, choice_num: i32) -> Option<String> {
    if story_id <= 0 || choice_num < 2 || choice_num > 6 || API.is_null() {
        return None;
    }
    let image = get_image();
    if image.is_null() {
        return None;
    }
    let wdm_class = find_class(
        image,
        to_cstr("Gallop").as_ptr(),
        to_cstr("WorkDataManager").as_ptr(),
    );
    if wdm_class.is_null() {
        return None;
    }
    let wdm = get_singleton(wdm_class);
    if !is_valid_user_ptr(wdm) {
        return None;
    }
    let sm = read_ptr_at(wdm, 96);
    if !is_valid_user_ptr(sm) {
        return None;
    }
    let chara = read_ptr_at(sm, 88);
    let mut cur_stats = [0i32; 5];
    let mut is_ill = false;
    if is_valid_user_ptr(chara) {
        cur_stats = [
            read_obscured_int_at(chara, 248),
            read_obscured_int_at(chara, 268),
            read_obscured_int_at(chara, 288),
            read_obscured_int_at(chara, 308),
            read_obscured_int_at(chara, 328),
        ];
        let eff_arr = read_ptr_at(chara, 1080);
        if is_valid_user_ptr(eff_arr) {
            is_ill = read_inline_obscured_int_array(eff_arr)
                .iter()
                .any(|&x| (1..=6).contains(&x));
        }
    }

    // 读取 WorkSingleModeData+440 EventChoiceRewardDict (Dictionary<ObscuredInt, EventChoiceReward>)
    let dict = read_ptr_at(sm, 440);
    if !is_valid_user_ptr(dict) {
        return None;
    }
    let count = read_int_at(dict, 0x20);
    let entries = read_ptr_at(dict, 0x18);
    if count <= 0 || count > 32 || !is_valid_user_ptr(entries) {
        return None;
    }
    // Entry<ObscuredInt, EventChoiceReward> 布局：+0x00 hashCode(i32), +0x04 next(i32), +0x08 ObscuredInt(20B), +0x20 val(ptr) => stride=0x28
    let mut reward_map: std::collections::HashMap<i32, *mut c_void> =
        std::collections::HashMap::new();
    let arr_len = seq_len(entries).min(64);
    let base = entries as *const u8;
    for i in 0..arr_len {
        let e_ptr = base.add(0x20 + i * 0x28);
        let hash_code = std::ptr::read_unaligned::<i32>(e_ptr as *const i32);
        if hash_code < 0 {
            continue;
        }
        let key_val = read_obscured_int_at(e_ptr as *const c_void, 0x08);
        let val_ptr = std::ptr::read_unaligned::<*mut c_void>(e_ptr.add(0x20) as *const *mut c_void);
        if !is_valid_user_ptr(val_ptr) {
            continue;
        }
        let vk = std::ptr::read_unaligned::<*mut c_void>(val_ptr as *const *mut c_void);
        if !is_valid_user_ptr(vk) || !get_class_name_from_pointer(vk).contains("EventChoiceReward") {
            continue;
        }
        reward_map.insert(key_val, val_ptr);
    }
    if reward_map.len() < (choice_num as usize) {
        return None;
    }

    // 自动匹配键口径：优先 1..=N（Cygames 标准 selectIndex），其次 0..N，其次 EVENT_CHOICES[i].gain_id
    let gain_ids: Vec<i32> = {
        let _lock = EVENT_STATE_MUTEX.lock();
        EVENT_CHOICES.iter().map(|c| c.gain_id).collect()
    };
    let use_one_based = (1..=choice_num).all(|k| reward_map.contains_key(&k));
    let use_zero_based = !use_one_based && (0..choice_num).all(|k| reward_map.contains_key(&k));
    let use_gain_id = !use_one_based
        && !use_zero_based
        && gain_ids.len() >= (choice_num as usize)
        && (0..(choice_num as usize)).all(|i| reward_map.contains_key(&gain_ids[i]));
    if !use_one_based && !use_zero_based && !use_gain_id {
        return None;
    }

    let mut choice_groups: Vec<String> = Vec::with_capacity(choice_num as usize);
    for idx in 0..(choice_num as usize) {
        let key = if use_one_based {
            (idx + 1) as i32
        } else if use_zero_based {
            idx as i32
        } else {
            gain_ids[idx]
        };
        let Some(&rew_obj) = reward_map.get(&key) else {
            return None;
        };
        let br_arr = read_ptr_at(rew_obj, 16); // _branchRewardArray: EventChoiceBranchReward[]
        if !is_valid_user_ptr(br_arr) {
            return None;
        }
        let br_len = seq_len(br_arr);
        if br_len == 0 || br_len > 8 {
            return None;
        }
        let ok_vals = decode_choice_branch_values(seq_item(br_arr, 0), cur_stats, is_ill)?;
        let ok_json = format!(
            r#"{{"Values":[{},{},{},{},{},{},{},{},{},{}]}}"#,
            ok_vals[0], ok_vals[1], ok_vals[2], ok_vals[3], ok_vals[4],
            ok_vals[5], ok_vals[6], ok_vals[7], ok_vals[8], ok_vals[9]
        );
        let fail_part = if br_len >= 2 {
            if let Some(ng_vals) = decode_choice_branch_values(seq_item(br_arr, 1), cur_stats, is_ill) {
                format!(
                    r#","FailedEffectValue":{{"Values":[{},{},{},{},{},{},{},{},{},{}]}}"#,
                    ng_vals[0], ng_vals[1], ng_vals[2], ng_vals[3], ng_vals[4],
                    ng_vals[5], ng_vals[6], ng_vals[7], ng_vals[8], ng_vals[9]
                )
            } else {
                String::new()
            }
        } else {
            String::new()
        };
        choice_groups.push(format!(
            r#"[{{"Option":"{}","SuccessEffectValue":{}{}}}]"#,
            idx + 1,
            ok_json,
            fail_part
        ));
    }

    Some(format!(
        r#"{{"Id":{},"Choices":[{}]}}"#,
        story_id,
        choice_groups.join(",")
    ))
}

/// Hook 1：AttachAdviceAutoPlayProxy.OnClickDecide() -> 覆盖全部 6 个教诲窗口 (T=2/11/23/35/47/59)
/// 原因：中后期 5 槽已满时 FestaAttachAdviceAction.ExecuteAction 因 CanDecide==true 直接调 OnClickDecide()
/// 跳过 AutoAttachAdvice()；改挂 OnClickDecide() 可在提交前用 matsuri 120 排列最优解覆写 5 槽，再调用原版提交。
extern "C" fn festa_attach_advice_hook_handler(proxy: *mut c_void) {
    let trampoline = unsafe { interceptor_get_trampoline(festa_attach_advice_hook_handler as usize) };
    if trampoline == 0 {
        unsafe { ura_log(1, "festa_attach_advice: trampoline not found"); }
        return;
    }
    type FnOnClickDecide = unsafe extern "C" fn(*mut c_void);
    let original: FnOnClickDecide = unsafe { std::mem::transmute(trampoline) };

    // 1) 独立 catch_unwind 包裹 AI 覆写：无论成功、跳过还是异常，末尾都必调 original(proxy) 提交
    let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| unsafe {
        if !matsuri_bridge::is_enabled() || !is_valid_user_ptr(proxy) || !matsuri_bridge::ready() {
            return;
        }
        let detach_addr = VM_ATTACH_DETACH_ALL_ADDR.load(std::sync::atomic::Ordering::Acquire);
        let quick_addr = VM_ATTACH_QUICK_ADDR.load(std::sync::atomic::Ordering::Acquire);
        let update_view_addr = VC_ATTACH_UPDATE_VIEW_ADDR.load(std::sync::atomic::Ordering::Acquire);
        if detach_addr == 0 || quick_addr == 0 || update_view_addr == 0 {
            return;
        }

        // 2) 在 READ_MUTEX + sigsetjmp 保护下读取 controller / vm / 持有教诲列表 / special 帧
        let read_res: Option<(*mut c_void, *mut c_void, Vec<(*mut c_void, i32, i32)>, String, i32, i32)> = {
            let _lock = READ_MUTEX.lock().unwrap_or_else(|e| e.into_inner());
            let jmp = sys_sigsetjmp(SIGSEGV_JMP_BUF.as_mut_ptr(), 1);
            if jmp != 0 {
                sigsegv_disarm();
                ura_log(2, "festa_attach_advice: sigsegv recovered, keeping native");
                None
            } else {
                sigsegv_arm();
                let res = (|| -> Option<(*mut c_void, *mut c_void, Vec<(*mut c_void, i32, i32)>, String, i32, i32)> {
                    let controller = read_ptr_at(proxy, 16);
                    if !is_valid_user_ptr(controller) {
                        return None;
                    }
                    let vm = read_ptr_at(controller, 96);
                    if !is_valid_user_ptr(vm) {
                        return None;
                    }
                    let (cur_turn, cur_epoch) = mt_peek_turn_fast();
                    let vm_turn = read_int_at(vm, 88);
                    let base_turn = if cur_turn >= 0 {
                        cur_turn
                    } else if vm_turn > 0 {
                        vm_turn - 1
                    } else {
                        2
                    };
                    let snapped = snap_to_advice_window(base_turn);
                    // 同局同窗口幂等去重，防止动画期间 OnClickDecide 重复触发覆写
                    if cur_epoch > 0
                        && ADVICE_LAST_EPOCH.load(std::sync::atomic::Ordering::Acquire) == cur_epoch
                        && ADVICE_LAST_WINDOW.load(std::sync::atomic::Ordering::Acquire) == snapped
                    {
                        return None;
                    }
                    let attach_ent = read_ptr_at(vm, 48);
                    if !is_valid_user_ptr(attach_ent) {
                        return None;
                    }
                    let owned_list = read_ptr_at(attach_ent, 16);
                    let entities = read_il2cpp_ref_seq(owned_list, 16);
                    if entities.len() < 5 {
                        return None;
                    }
                    let mut owned: Vec<(*mut c_void, i32, i32)> = Vec::new();
                    for ent in entities {
                        let main_vo = read_ptr_at(ent, 16);
                        let main_id = if is_valid_user_ptr(main_vo) {
                            read_int_at(main_vo, 16)
                        } else {
                            0
                        };
                        let fusion_vo = read_ptr_at(ent, 24);
                        let fusion_id = if is_valid_user_ptr(fusion_vo) {
                            read_int_at(fusion_vo, 16)
                        } else {
                            0
                        };
                        if main_id > 0 || fusion_id > 0 {
                            owned.push((ent, main_id, fusion_id));
                        }
                    }
                    if owned.len() < 5 {
                        return None;
                    }
                    let frame = build_matsuri_frame_ex("special", 51, Some(snapped), None, None);
                    Some((controller, vm, owned, frame, cur_epoch, snapped))
                })();
                sigsegv_disarm();
                res
            }
        };

        let Some((controller, vm, owned, frame, cur_epoch, snapped)) = read_res else {
            return;
        };
        if frame.is_empty() || frame.contains("\"error\"") {
            return;
        }

        // 3) 调 matsuri 算 5 槽最优教诲排列（<3ms）
        let want_ids = match matsuri_bridge::decide_advice(&frame) {
            Ok(ids) => ids,
            Err(e) => {
                ura_log(3, &format!("festa_attach_advice: T{snapped} fallback native ({e})"));
                return;
            }
        };

        // 4) 精确匹配 5 个互不重复的 AdviceEntity 指针（支持有效 ID 或基础 ID 匹配）
        let mut matched: [*mut c_void; 5] = [std::ptr::null_mut(); 5];
        let mut used = vec![false; owned.len()];
        for (slot, &want) in want_ids.iter().enumerate() {
            let mut found = None;
            for (i, &(ent, main_id, fusion_id)) in owned.iter().enumerate() {
                if used[i] {
                    continue;
                }
                let eff_id = if fusion_id > 0 { fusion_id } else { main_id };
                if eff_id == want || main_id == want {
                    found = Some((i, ent));
                    break;
                }
            }
            if let Some((i, ent)) = found {
                used[i] = true;
                matched[slot] = ent;
            } else {
                ura_log(
                    2,
                    &format!(
                        "festa_attach_advice: T{snapped} unmatched want_id={} in owned={:?}, keeping native",
                        want,
                        owned.iter().map(|(_, m, f)| (*m, *f)).collect::<Vec<_>>()
                    ),
                );
                return;
            }
        }

        // 5) 调官方 ViewModel/ViewController 方法原子覆写并刷新 UI
        type FnVoid0 = unsafe extern "C" fn(*mut c_void);
        type FnQuick = unsafe extern "C" fn(*mut c_void, i32, *mut c_void);
        let detach_fn: FnVoid0 = std::mem::transmute(detach_addr);
        let quick_fn: FnQuick = std::mem::transmute(quick_addr);
        let update_view_fn: FnVoid0 = std::mem::transmute(update_view_addr);

        detach_fn(vm);
        for (slot, &ent) in matched.iter().enumerate() {
            quick_fn(vm, (slot + 1) as i32, ent);
        }
        update_view_fn(controller);
        if cur_epoch > 0 {
            ADVICE_LAST_EPOCH.store(cur_epoch, std::sync::atomic::Ordering::Release);
            ADVICE_LAST_WINDOW.store(snapped, std::sync::atomic::Ordering::Release);
        }
        let hits = ADVICE_OVERRIDE_HITS.fetch_add(1, std::sync::atomic::Ordering::Relaxed) + 1;
        ura_log(
            3,
            &format!("★ festa_attach_advice OVERRIDE #{hits}: T{snapped} want={:?}", want_ids),
        );
    }));

    // 6) 最后调用原版 OnClickDecide 提交
    let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| unsafe {
        original(proxy);
    }));
}

/// Hook 2：FusionAdviceAutoPlayProxy.AutoFusionAdvice() -> 先跑原版保底，再按 matsuri FusionConfig 覆写配对
extern "C" fn festa_fusion_advice_hook_handler(proxy: *mut c_void) {
    let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| unsafe {
        let trampoline = interceptor_get_trampoline(festa_fusion_advice_hook_handler as usize);
        if trampoline == 0 {
            ura_log(1, "festa_fusion_advice: trampoline not found");
            return;
        }
        type FnAutoFusion = unsafe extern "C" fn(*mut c_void);
        let original: FnAutoFusion = std::mem::transmute(trampoline);
        // 1) 先调用原版确保 UI 与 Decide 状态完备
        original(proxy);

        if !matsuri_bridge::is_enabled() || !is_valid_user_ptr(proxy) {
            return;
        }
        let reset_addr = VM_FUSION_RESET_ADDR.load(std::sync::atomic::Ordering::Acquire);
        let update_addr = VM_FUSION_UPDATE_ADDR.load(std::sync::atomic::Ordering::Acquire);
        let update_view_addr = VC_FUSION_UPDATE_VIEW_ADDR.load(std::sync::atomic::Ordering::Acquire);
        if reset_addr == 0 || update_addr == 0 || update_view_addr == 0 {
            return;
        }

        // 2) 安全读取 base_vo_map / target_vo_map
        let read_res: Option<(
            *mut c_void,
            *mut c_void,
            std::collections::HashMap<i32, *mut c_void>,
            std::collections::HashMap<i32, *mut c_void>,
        )> = {
            let _lock = READ_MUTEX.lock().unwrap_or_else(|e| e.into_inner());
            let jmp = sys_sigsetjmp(SIGSEGV_JMP_BUF.as_mut_ptr(), 1);
            if jmp != 0 {
                sigsegv_disarm();
                ura_log(2, "festa_fusion_advice: sigsegv recovered, keeping native");
                None
            } else {
                sigsegv_arm();
                let res = (|| -> Option<(
                    *mut c_void,
                    *mut c_void,
                    std::collections::HashMap<i32, *mut c_void>,
                    std::collections::HashMap<i32, *mut c_void>,
                )> {
                    let controller = read_ptr_at(proxy, 16);
                    if !is_valid_user_ptr(controller) {
                        return None;
                    }
                    let vm = read_ptr_at(controller, 48);
                    if !is_valid_user_ptr(vm) {
                        return None;
                    }
                    let mut base_map: std::collections::HashMap<i32, *mut c_void> =
                        std::collections::HashMap::new();
                    let mut target_map: std::collections::HashMap<i32, *mut c_void> =
                        std::collections::HashMap::new();

                    let base_list = read_ptr_at(vm, 40);
                    for ent in read_il2cpp_ref_seq(base_list, 16) {
                        let vo = read_ptr_at(ent, 16);
                        if is_valid_user_ptr(vo) {
                            let id = read_int_at(vo, 16);
                            if (101..=599).contains(&id) {
                                base_map.insert(id, vo);
                            }
                        }
                    }

                    let target_list = read_ptr_at(vm, 32);
                    for ent in read_il2cpp_ref_seq(target_list, 16) {
                        for off in [24i32, 16i32] {
                            let vo = read_ptr_at(ent, off);
                            if is_valid_user_ptr(vo) {
                                let id = read_int_at(vo, 16);
                                if (601..=1099).contains(&id) {
                                    target_map.insert(id, vo);
                                }
                            }
                        }
                    }

                    if target_map.is_empty() {
                        let master_list = read_ptr_at(vm, 48);
                        for vo in read_il2cpp_ref_seq(master_list, 32) {
                            let id = read_int_at(vo, 16);
                            if (101..=599).contains(&id) {
                                base_map.entry(id).or_insert(vo);
                            }
                        }
                    }

                    if base_map.is_empty() || target_map.is_empty() {
                        return None;
                    }
                    Some((controller, vm, base_map, target_map))
                })();
                sigsegv_disarm();
                res
            }
        };

        let Some((controller, vm, base_map, target_map)) = read_res else {
            return;
        };

        let is_year3 = target_map.contains_key(&801)
            || target_map.contains_key(&901)
            || target_map.contains_key(&1001);
        let desired = matsuri_bridge::fusion_pairs(is_year3);

        let mut used_base: std::collections::HashSet<i32> = std::collections::HashSet::new();
        let mut plan: Vec<(i32, i32, *mut c_void, *mut c_void)> = Vec::new();
        for (want_base, want_target) in desired {
            let Some(&target_vo) = target_map.get(&want_target) else {
                continue;
            };
            let chosen_base = if base_map.contains_key(&want_base) && !used_base.contains(&want_base) {
                Some(want_base)
            } else {
                base_map
                    .keys()
                    .copied()
                    .filter(|b| !used_base.contains(b))
                    .min()
            };
            let Some(actual_base) = chosen_base else {
                ura_log(2, "festa_fusion_advice: insufficient base advices, keeping native");
                return;
            };
            let Some(&base_vo) = base_map.get(&actual_base) else {
                return;
            };
            used_base.insert(actual_base);
            plan.push((actual_base, want_target, base_vo, target_vo));
        }

        if plan.len() != target_map.len() || plan.is_empty() {
            ura_log(
                2,
                &format!(
                    "festa_fusion_advice: plan len {} != target len {}, keeping native",
                    plan.len(),
                    target_map.len()
                ),
            );
            return;
        }

        type FnVoid0 = unsafe extern "C" fn(*mut c_void);
        type FnUpdateFusion = unsafe extern "C" fn(*mut c_void, *mut c_void, *mut c_void);
        let reset_fn: FnVoid0 = std::mem::transmute(reset_addr);
        let update_fn: FnUpdateFusion = std::mem::transmute(update_addr);
        let update_view_fn: FnVoid0 = std::mem::transmute(update_view_addr);

        reset_fn(vm);
        for &(_, _, base_vo, target_vo) in &plan {
            update_fn(vm, base_vo, target_vo);
        }
        update_view_fn(controller);

        let desc = plan
            .iter()
            .map(|(b, t, _, _)| format!("{b}->{t}"))
            .collect::<Vec<_>>()
            .join(",");
        matsuri_bridge::record_fusion(desc.clone());
        let hits = FUSION_OVERRIDE_HITS.fetch_add(1, std::sync::atomic::Ordering::Relaxed) + 1;
        ura_log(3, &format!("★ festa_fusion_advice OVERRIDE #{hits}: {desc}"));
    }));
}

/// Hook 3：StoryViewProxy.TryChoice(index: i32) -> bool（全事件 AI 接管：优先本地剧本规则，全部其余事件读原生 EventChoiceRewardDict 实时估值）
extern "C" fn story_try_choice_hook_handler(proxy: *mut c_void, index: i32) -> bool {
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| unsafe {
        let trampoline = interceptor_get_trampoline(story_try_choice_hook_handler as usize);
        if trampoline == 0 {
            ura_log(1, "story_try_choice: trampoline not found");
            return false;
        }
        type FnTryChoice = unsafe extern "C" fn(*mut c_void, i32) -> bool;
        let original: FnTryChoice = std::mem::transmute(trampoline);

        let mut final_idx = index;
        if matsuri_bridge::is_enabled() && is_valid_user_ptr(proxy) && matsuri_bridge::ready() {
            let story_id = {
                let _lock = EVENT_STATE_MUTEX.lock();
                EVENT_STORY_ID
            };
            let choice_num = {
                let get_num_addr = PROXY_GET_CHOICE_NUM_ADDR.load(std::sync::atomic::Ordering::Acquire);
                if get_num_addr != 0 {
                    type FnGetNum = unsafe extern "C" fn(*mut c_void) -> i32;
                    let f: FnGetNum = std::mem::transmute(get_num_addr);
                    f(proxy)
                } else {
                    0
                }
            };
            if story_id > 0 && choice_num > 1 {
                let ev_frame: Option<(String, bool)> = {
                    let _lock = READ_MUTEX.lock().unwrap_or_else(|e| e.into_inner());
                    let jmp = sys_sigsetjmp(SIGSEGV_JMP_BUF.as_mut_ptr(), 1);
                    if jmp != 0 {
                        sigsegv_disarm();
                        None
                    } else {
                        sigsegv_arm();
                        let story_json = build_live_story_json(story_id, choice_num);
                        let has_live_story = story_json.is_some();
                        let fr = build_matsuri_frame_ex(
                            "event",
                            1,
                            None,
                            Some(story_id),
                            story_json.as_deref(),
                        );
                        sigsegv_disarm();
                        Some((fr, has_live_story))
                    }
                };
                if let Some((fr, has_live_story)) = ev_frame {
                    if !fr.is_empty() && !fr.contains("\"error\"") {
                        if let Ok(Some(chosen)) = matsuri_bridge::decide_event(&fr) {
                            let c = chosen as i32;
                            if c >= 0 && c < choice_num {
                                final_idx = c;
                                let hits = EVENT_OVERRIDE_HITS
                                    .fetch_add(1, std::sync::atomic::Ordering::Relaxed)
                                    + 1;
                                ura_log(
                                    3,
                                    &format!(
                                        "★ story_try_choice OVERRIDE #{hits}: sid={story_id} live_reward={has_live_story} orig={index} -> ai={final_idx} (n={choice_num})"
                                    ),
                                );
                            }
                        }
                    }
                }
            }
        }
        original(proxy, final_idx)
    }));
    result.unwrap_or(false)
}

/// ★ ci_patch19.1: 按精确类名在 image 中扫描（兼容嵌套类，不依赖 Android 2.33.2 未导出的 il2cpp_class_get_declaring_type）
unsafe fn find_class_exact_in_image(image: *const c_void, exact_name: &str) -> *mut c_void {
    let get_count_fn = resolve_il2cpp_symbol("il2cpp_image_get_class_count");
    let get_class_fn = resolve_il2cpp_symbol("il2cpp_image_get_class");
    let get_name_fn = resolve_il2cpp_symbol("il2cpp_class_get_name");
    if get_count_fn.is_null() || get_class_fn.is_null() || get_name_fn.is_null() {
        return std::ptr::null_mut();
    }
    let get_count: FnImageGetClassCount = std::mem::transmute(get_count_fn);
    let get_class: FnImageGetClass = std::mem::transmute(get_class_fn);
    let get_name: unsafe extern "C" fn(*const c_void) -> *const c_char =
        std::mem::transmute(get_name_fn);
    let count = get_count(image);
    for i in 0..count {
        let cls = get_class(image, i);
        if cls.is_null() {
            continue;
        }
        let np = get_name(cls);
        if np.is_null() {
            continue;
        }
        if CStr::from_ptr(np).to_string_lossy() == exact_name {
            return cls as *mut c_void;
        }
    }
    std::ptr::null_mut()
}

unsafe fn install_festa_advice_and_event_hooks() {
    if FESTA_ADVICE_HOOKS_INSTALLED.swap(true, std::sync::atomic::Ordering::AcqRel) {
        return;
    }
    if API.is_null() {
        FESTA_ADVICE_HOOKS_INSTALLED.store(false, std::sync::atomic::Ordering::Release);
        return;
    }
    let image = get_image();
    if image.is_null() {
        FESTA_ADVICE_HOOKS_INSTALLED.store(false, std::sync::atomic::Ordering::Release);
        return;
    }

    // ---- 1) 教诲附着 ViewModel / ViewController 辅助方法 + AutoPlayProxy hook ----
    let attach_vm_cls = find_class(
        image,
        to_cstr("Gallop").as_ptr(),
        to_cstr("SingleModeScenarioFestaAttachAdviceViewModel").as_ptr(),
    );
    let attach_vc_cls = find_class(
        image,
        to_cstr("Gallop").as_ptr(),
        to_cstr("SingleModeScenarioFestaAttachAdviceViewController").as_ptr(),
    );
    if !attach_vm_cls.is_null() {
        let a_detach = find_method_entry_exact(attach_vm_cls, "DetachAllAdvice", 0);
        let a_quick = find_method_entry_exact(attach_vm_cls, "UpdateAttachAdviceQuick", 2);
        VM_ATTACH_DETACH_ALL_ADDR.store(a_detach, std::sync::atomic::Ordering::Release);
        VM_ATTACH_QUICK_ADDR.store(a_quick, std::sync::atomic::Ordering::Release);
    }
    if !attach_vc_cls.is_null() {
        let a_upd = find_method_entry_exact(attach_vc_cls, "UpdateAttachAdviceView", 0);
        VC_ATTACH_UPDATE_VIEW_ADDR.store(a_upd, std::sync::atomic::Ordering::Release);
    }
    let attach_proxy_cls = find_class_exact_in_image(image, "AttachAdviceAutoPlayProxy");
    if !attach_proxy_cls.is_null() {
        let addr = find_method_entry_exact(attach_proxy_cls, "OnClickDecide", 0);
        if addr != 0 {
            install_hook_safe(
                "FestaAttachAdvice",
                addr,
                festa_attach_advice_hook_handler as usize,
                &mut ORIG_ATTACH_ADVICE_PROLOGUE,
            );
            set_hook_status(
                "festa.attach_advice",
                &format!(
                    "resolved@0x{:x} OnClickDecide (d=0x{:x} q=0x{:x} v=0x{:x})",
                    addr,
                    VM_ATTACH_DETACH_ALL_ADDR.load(std::sync::atomic::Ordering::Relaxed),
                    VM_ATTACH_QUICK_ADDR.load(std::sync::atomic::Ordering::Relaxed),
                    VC_ATTACH_UPDATE_VIEW_ADDR.load(std::sync::atomic::Ordering::Relaxed),
                ),
            );
        } else {
            set_hook_status("festa.attach_advice", "failed:method_not_found");
        }
    } else {
        set_hook_status("festa.attach_advice", "failed:class_not_found");
    }

    // ---- 2) 教诲転成 ViewModel / ViewController 辅助方法 + AutoPlayProxy hook ----
    let fusion_vm_cls = find_class(
        image,
        to_cstr("Gallop").as_ptr(),
        to_cstr("SingleModeScenarioFestaFusionAdviceViewModel").as_ptr(),
    );
    let fusion_vc_cls = find_class(
        image,
        to_cstr("Gallop").as_ptr(),
        to_cstr("SingleModeScenarioFestaFusionAdviceViewController").as_ptr(),
    );
    if !fusion_vm_cls.is_null() {
        let a_reset = find_method_entry_exact(fusion_vm_cls, "ResetAllFusionTargetAdviceVoList", 0);
        let a_upd = find_method_entry_exact(fusion_vm_cls, "UpdateFusionTargetAdviceVoList", 2);
        VM_FUSION_RESET_ADDR.store(a_reset, std::sync::atomic::Ordering::Release);
        VM_FUSION_UPDATE_ADDR.store(a_upd, std::sync::atomic::Ordering::Release);
    }
    if !fusion_vc_cls.is_null() {
        let a_view = find_method_entry_exact(fusion_vc_cls, "UpdateOnChangeFusionAdvice", 0);
        VC_FUSION_UPDATE_VIEW_ADDR.store(a_view, std::sync::atomic::Ordering::Release);
    }
    let fusion_proxy_cls = find_class_exact_in_image(image, "FusionAdviceAutoPlayProxy");
    if !fusion_proxy_cls.is_null() {
        let addr = find_method_entry_exact(fusion_proxy_cls, "AutoFusionAdvice", 0);
        if addr != 0 {
            install_hook_safe(
                "FestaFusionAdvice",
                addr,
                festa_fusion_advice_hook_handler as usize,
                &mut ORIG_FUSION_ADVICE_PROLOGUE,
            );
            set_hook_status(
                "festa.fusion_advice",
                &format!(
                    "resolved@0x{:x} (r=0x{:x} u=0x{:x} v=0x{:x})",
                    addr,
                    VM_FUSION_RESET_ADDR.load(std::sync::atomic::Ordering::Relaxed),
                    VM_FUSION_UPDATE_ADDR.load(std::sync::atomic::Ordering::Relaxed),
                    VC_FUSION_UPDATE_VIEW_ADDR.load(std::sync::atomic::Ordering::Relaxed),
                ),
            );
        } else {
            set_hook_status("festa.fusion_advice", "failed:method_not_found");
        }
    } else {
        set_hook_status("festa.fusion_advice", "failed:class_not_found");
    }

    // ---- 3) 事件选项 StoryViewProxy.TryChoice(index) hook ----
    let story_proxy_cls = find_class_exact_in_image(image, "StoryViewProxy");
    if !story_proxy_cls.is_null() {
        let num_addr = find_method_entry_exact(story_proxy_cls, "GetChoiceButtonNum", 0);
        PROXY_GET_CHOICE_NUM_ADDR.store(num_addr, std::sync::atomic::Ordering::Release);
        let try_addr = find_method_entry_exact(story_proxy_cls, "TryChoice", 1);
        if try_addr != 0 {
            install_hook_safe(
                "StoryTryChoice",
                try_addr,
                story_try_choice_hook_handler as usize,
                &mut ORIG_STORY_TRY_CHOICE_PROLOGUE,
            );
            set_hook_status(
                "event.try_choice",
                &format!("resolved@0x{:x} (num=0x{:x})", try_addr, num_addr),
            );
        } else {
            set_hook_status("event.try_choice", "failed:method_not_found");
        }
    } else {
        set_hook_status("event.try_choice", "failed:class_not_found");
    }
}'''

# 4) 在 push_loop 与 on_game_initialized() 两处均安装 3 个新 hook（幂等 swap 保护，总 hook 6 -> 9）
PUSH_INIT_HOOK_OLD = r'''                install_failure_rate_hook();
                install_event_choice_hook();
                flog("init", &format!("plugin {} boot, core hooks ready", PLUGIN_VERSION));'''

PUSH_INIT_HOOK_NEW = r'''                install_failure_rate_hook();
                install_event_choice_hook();
                install_festa_advice_and_event_hooks();
                flog("init", &format!("plugin {} boot, core hooks ready", PLUGIN_VERSION));'''

INIT_HOOK_OLD = r'''        install_exec_training_hook();
        install_failure_rate_hook();
        install_event_choice_hook();'''

INIT_HOOK_NEW = r'''        install_exec_training_hook();
        install_failure_rate_hook();
        install_event_choice_hook();
        install_festa_advice_and_event_hooks();'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch19: {msg}")
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
        s = _replace_once(s, FRAME_SIG_OLD, FRAME_SIG_NEW, "frame_sig_ex")
        s = _replace_once(s, FRAME_TURN_OLD, FRAME_TURN_NEW, "frame_turn_override")
        s = _replace_once(s, FRAME_JSON_TAIL_OLD, FRAME_JSON_TAIL_NEW, "frame_json_story_source")
        s = _replace_once(s, WEIGHT_TOGGLE_OLD, WEIGHT_TOGGLE_NEW, "weight_hot_toggle")
        s = _replace_once(s, HOOK_IMPL_ANCHOR, HOOK_IMPL_ADD, "festa_advice_fusion_event_hooks")
        s = _replace_once(s, PUSH_INIT_HOOK_OLD, PUSH_INIT_HOOK_NEW, "push_loop_install_patch19")
        s = _replace_once(s, INIT_HOOK_OLD, INIT_HOOK_NEW, "on_game_init_install_patch19")
    except RuntimeError as e:
        return _fail(str(e))

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    for needle in (
        "fn build_matsuri_frame_ex",
        "fn find_method_entry_exact",
        "fn read_il2cpp_ref_seq",
        "fn build_live_story_json",
        "fn decode_choice_branch_values",
        "fn festa_attach_advice_hook_handler",
        "fn festa_fusion_advice_hook_handler",
        "fn story_try_choice_hook_handler",
        "install_festa_advice_and_event_hooks",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())
