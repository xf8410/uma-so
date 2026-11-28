//! matsuri 完整决策栈接入（`matsuri-ai` 在线档）
//!
//! 与 `matsuri_scorer.rs` 的区别：那个是抄权重另写的近似打分器（7 个候选、
//! 每项训练只有主/副收益、失败率只有一个字段、无守门链、无事件/教诲/比赛），
//! 本模块是原版决策器：
//!
//! ```text
//! Frame JSON (游戏现场)
//!   → matsuri_ai::ParsedFrame::parse        引擎通用段 + 剧本 FestaState
//!   → BaseStatus::to_base_game              → BaseGame
//!   → MatsuriGame::restore                  → 可继续决策的局面
//!   → route()                               Train / Advice / Event / Skip
//!   → MatsuriTrainer / MatsuriSearchTrainer → DecisionRecord
//! ```
//!
//! 纪律（照抄上游 lib.rs）：
//! - 输入只走 `ParsedFrame::parse`，不接受"近似构造的局面"；
//! - 帧为 `thisTurn.json` 格式，`baseGame.scenarioId` 必须是 15；
//! - 同内容帧按 digest 幂等，重复帧不重复决策；
//! - 引擎全局数据只初始化一次，失败即停，不退化。

use std::path::Path;
use std::sync::{Mutex, OnceLock};
use std::time::Instant;

use matsuri_ai::session::{ensure_data as engine_init_data, OnlineSession, TrainerKind};
use matsuri_ai::OnlineTurnOutput;
use matsuri_core::config::MatsuriConfig;
use matsuri_core::data::MatsuriData;
use matsuri_core::game::Action;

/// 运行时数据根（应用自有目录，targetSdk=36 下无需任何存储权限）
pub const DATA_ROOT: &str =
    "/sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/matsuri_data";
/// 可选搜索调参文件（无需重打包即可切换 search_n / mode / threads / wait_ms）
pub const SEARCH_CFG_PATH: &str =
    "/sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/ai_search.txt";

/// 剧本资产文件（`MatsuriData::parse` 的输入，随 so 内嵌）
const SCENARIO_ASSET: &str = include_str!("../../vendor/matsuri/data/scenario_matsuri.json");
/// 剧本配置（融合预设 / 卡组档位，随 so 内嵌）
const MATSURI_CFG: &str = include_str!("../../vendor/matsuri/data/matsuri_config.toml");
/// 搜索蒸馏权重（与手写/rollout 教师配套的残差策略，随 so 内嵌）
const DISTILLED_CFG: &str = include_str!("../../vendor/matsuri/data/distilled.toml");
/// 引擎配置模板（sim / search / record / output；uma / cards / seed 运行时覆盖）
const AI_CFG: &str = include_str!("../../vendor/matsuri/data/ai_config.toml");

// 默认轻量 MCTS 搜索参数（兼顾前瞻提分与真机/模拟器流畅度）
const DEFAULT_SEARCH_N: usize = 128;
const DEFAULT_GROUP_SIZE: usize = 32;
const DEFAULT_SEARCH_THREADS: usize = 4;
const DEFAULT_WAIT_BUDGET_MS: u64 = 1500;

#[derive(Clone, Copy, Debug)]
struct SearchRuntimeCfg {
    kind: TrainerKind,
    search_n: usize,
    group_size: usize,
    threads: usize,
    wait_ms: u64,
}

impl Default for SearchRuntimeCfg {
    fn default() -> Self {
        Self {
            kind: TrainerKind::Search,
            search_n: DEFAULT_SEARCH_N,
            group_size: DEFAULT_GROUP_SIZE,
            threads: DEFAULT_SEARCH_THREADS,
            wait_ms: DEFAULT_WAIT_BUDGET_MS,
        }
    }
}

/// 从可选配置文件读取搜索参数（缺文件则用默认轻量 MCTS：Search / search_n=128 / group=32 / threads=4）
fn load_search_runtime_cfg() -> SearchRuntimeCfg {
    let mut cfg = SearchRuntimeCfg::default();
    let Ok(text) = std::fs::read_to_string(SEARCH_CFG_PATH) else {
        return cfg;
    };
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some((k, v)) = line.split_once('=') else {
            continue;
        };
        let k = k.trim();
        let v = v.trim();
        match k {
            "mode" => {
                if v.eq_ignore_ascii_case("hand") || v.eq_ignore_ascii_case("matsuri") {
                    cfg.kind = TrainerKind::Hand;
                } else if v.eq_ignore_ascii_case("search") {
                    cfg.kind = TrainerKind::Search;
                }
            }
            "search_n" => {
                if let Ok(n) = v.parse::<usize>() {
                    if n == 0 {
                        cfg.kind = TrainerKind::Hand;
                        cfg.search_n = 0;
                    } else {
                        cfg.search_n = n.clamp(16, 16384);
                    }
                }
            }
            "group_size" => {
                if let Ok(g) = v.parse::<usize>() {
                    cfg.group_size = g.clamp(8, 1024);
                }
            }
            "threads" => {
                if let Ok(t) = v.parse::<usize>() {
                    cfg.threads = t.clamp(1, 12);
                }
            }
            "wait_ms" => {
                if let Ok(ms) = v.parse::<u64>() {
                    cfg.wait_ms = ms.clamp(100, 15000);
                }
            }
            _ => {}
        }
    }
    if cfg.search_n == 0 {
        cfg.kind = TrainerKind::Hand;
    }
    cfg
}

/// 主线程遇到回合切换时允许同步等待 worker 完成本回合决策的最长毫秒数
pub fn wait_budget_ms() -> u64 {
    if let Ok(guard) = cell().lock() {
        if let Some(b) = guard.as_ref() {
            return b.rt_cfg.wait_ms;
        }
    }
    DEFAULT_WAIT_BUDGET_MS
}

/// 运行时热开关：检查 `ai_search.txt` 中是否配置了 `enabled=false/0` 或 `disabled=true/1`（每 2 秒最多读一次文件）
static AI_ENABLED_CACHE: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(true);
static AI_ENABLED_AT_MS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

pub fn is_enabled() -> bool {
    use std::sync::atomic::Ordering::Relaxed;
    let now_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);
    let last = AI_ENABLED_AT_MS.load(Relaxed);
    if last != 0 && now_ms.saturating_sub(last) < 2000 {
        return AI_ENABLED_CACHE.load(Relaxed);
    }
    AI_ENABLED_AT_MS.store(now_ms, Relaxed);
    let mut en = true;
    if let Ok(text) = std::fs::read_to_string(SEARCH_CFG_PATH) {
        for line in text.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            if let Some((k, v)) = line.split_once('=') {
                let k = k.trim();
                let v = v.trim();
                if k.eq_ignore_ascii_case("enabled") {
                    if v.eq_ignore_ascii_case("false") || v == "0" || v.eq_ignore_ascii_case("off") {
                        en = false;
                    } else if v.eq_ignore_ascii_case("true") || v == "1" || v.eq_ignore_ascii_case("on") {
                        en = true;
                    }
                } else if k.eq_ignore_ascii_case("disabled") {
                    if v.eq_ignore_ascii_case("true") || v == "1" {
                        en = false;
                    }
                }
            }
        }
    }
    AI_ENABLED_CACHE.store(en, Relaxed);
    en
}

// ===== 动作标记 =====
//
// 把 matsuri 的 `Action` 压成一个稳定的 i32，供 hook 侧按 omakase 行检索。
//
// omakase 编号（ARM64 反汇编 + master.mdb + 单变量实测锁定，游戏 2.33.2 / setting 1501）：
//   150101..150105 = Train(0..4)     速 耐 力 根 智（与 training.normal[] 顺序一致）
//   150120..150124 = ActTrain(0..4)  演目下标（与 scenario_matsuri.json acts[].train 一致）
//   150106         = Rest            休息 / 合宿休息（GetHolidayBaseWeight / GetSummerCampBaseWeight）
//   150107         = FriendOuting    友人 / 团队出行（GetOutingBaseWeight）
//   150108         = NormalOuting    育成马娘普通外出（GetOutingTrainingCharaBaseWeight）
//   150109         = Clinic          保健室（GetHospitalBaseWeight）
//   150110         = Race            比赛（GetRaceBaseWeight）
pub const TAG_TRAIN_BASE: i32 = 1000;
pub const TAG_ACTTRAIN_BASE: i32 = 2000;
pub const TAG_RACE: i32 = 3000;
pub const TAG_REST: i32 = 3001;
pub const TAG_NORMAL_OUTING: i32 = 3002;
pub const TAG_FRIEND_OUTING: i32 = 3003;
pub const TAG_CLINIC: i32 = 3004;
pub const TAG_ADVICE: i32 = 4000;

/// matsuri 动作 → 稳定标记
pub fn action_tag(action: &Action) -> i32 {
    match action {
        Action::Train(i) => TAG_TRAIN_BASE + i32::from(*i),
        Action::ActTrain(i) => TAG_ACTTRAIN_BASE + i32::from(*i),
        Action::Race => TAG_RACE,
        Action::Rest => TAG_REST,
        Action::NormalOuting => TAG_NORMAL_OUTING,
        Action::FriendOuting => TAG_FRIEND_OUTING,
        Action::Clinic => TAG_CLINIC,
        Action::AssignAdvice(_) => TAG_ADVICE,
    }
}

/// omakase 行号 → 动作标记；不是可接管的动作返回 None
pub fn omakase_arg_to_tag(arg: i32) -> Option<i32> {
    match arg {
        150101..=150105 => Some(TAG_TRAIN_BASE + (arg - 150101)),
        150120..=150124 => Some(TAG_ACTTRAIN_BASE + (arg - 150120)),
        150106 => Some(TAG_REST),
        150107 => Some(TAG_FRIEND_OUTING),
        150108 => Some(TAG_NORMAL_OUTING),
        150109 => Some(TAG_CLINIC),
        150110 => Some(TAG_RACE),
        _ => None,
    }
}

/// 一次决策的产物
#[derive(Clone, Debug, Default)]
pub struct TurnDecision {
    pub turn: i32,
    pub epoch: u64,
    pub kind: &'static str,
    pub chosen: i32,
    pub action_desc: String,
    pub candidate_n: usize,
    pub search_backed: bool,
    pub elapsed_ms: u64,
    pub note: String,
    /// 候选分（下标, 分数）
    pub scores: Vec<(usize, f64)>,
    /// 候选动作标记与分数 `(tag, score)`；与 `scores` 同序但带语义
    pub actions: Vec<(i32, f64)>,
    /// 中选动作的标记（`skip` 为 -1）
    pub chosen_tag: i32,
}

struct Bridge {
    session: OnlineSession,
    rt_cfg: SearchRuntimeCfg,
    distilled_loaded: bool,
    fusion_year2: Vec<(i32, i32)>,
    fusion_year3: Vec<(i32, i32)>,
    decided: u64,
    skipped: u64,
    failed: u64,
    advice_decided: u64,
    advice_last: String,
    fusion_applied: u64,
    fusion_last: String,
    event_decided: u64,
    event_last: String,
    last_elapsed_ms: u64,
    last_error: String,
    last: Option<TurnDecision>,
}

static BRIDGE: OnceLock<Mutex<Option<Bridge>>> = OnceLock::new();
/// 引擎全局数据初始化状态：仅成功后缓存 true；失败允许后续重试，避免 OnceLock 永久锁死
static DATA_INIT: OnceLock<Mutex<Option<Result<(), String>>>> = OnceLock::new();
/// Android Bionic 默认 pthread 栈仅 1MB；显式初始化 rayon 全局池（4MB 栈 + 限核），防栈溢出和抢占渲染线程
static RAYON_INIT: OnceLock<usize> = OnceLock::new();

fn cell() -> &'static Mutex<Option<Bridge>> {
    BRIDGE.get_or_init(|| Mutex::new(None))
}

fn data_init_cell() -> &'static Mutex<Option<Result<(), String>>> {
    DATA_INIT.get_or_init(|| Mutex::new(None))
}

fn ensure_rayon_pool(threads: usize) -> usize {
    *RAYON_INIT.get_or_init(|| {
        let avail = std::thread::available_parallelism()
            .map(|n| n.get())
            .unwrap_or(4);
        let n = threads.min(avail).clamp(1, 12);
        let _ = rayon::ThreadPoolBuilder::new()
            .num_threads(n)
            .stack_size(4 * 1024 * 1024)
            .thread_name(|i| format!("matsuri-rayon-{i}"))
            .build_global();
        n
    })
}

/// 内嵌的 6 张引擎表（CI 生成到 vendor/gamedata/）
const EMBEDDED_GAMEDATA: &[(&str, &str)] = &[
    ("constants.json", include_str!("../../vendor/gamedata/constants.json")),
    ("umaDB.json", include_str!("../../vendor/gamedata/umaDB.json")),
    ("cardDB.json", include_str!("../../vendor/gamedata/cardDB.json")),
    ("events.json", include_str!("../../vendor/gamedata/events.json")),
    ("text_data_dict.json", include_str!("../../vendor/gamedata/text_data_dict.json")),
    ("manifest.json", include_str!("../../vendor/gamedata/manifest.json")),
];

/// 把内嵌资产完整且原子地展开到运行时目录（逐文件校验字节长度，防止半截文件残留）
fn ensure_assets() -> Result<(), String> {
    let root = Path::new(DATA_ROOT);
    std::fs::create_dir_all(root).map_err(|e| format!("mkdir {DATA_ROOT}: {e}"))?;
    for (name, body) in EMBEDDED_GAMEDATA {
        let path = root.join(name);
        let expected_len = body.len() as u64;
        if let Ok(meta) = std::fs::metadata(&path) {
            if meta.len() == expected_len {
                continue;
            }
        }
        let tmp = root.join(format!("{name}.tmp"));
        std::fs::write(&tmp, body).map_err(|e| format!("write {}: {e}", tmp.display()))?;
        std::fs::rename(&tmp, &path)
            .map_err(|e| format!("rename {} -> {}: {e}", tmp.display(), path.display()))?;
    }
    Ok(())
}

/// 引擎全局数据初始化（成功后进程内只执行一次；失败记录原因并允许后续重试）
fn ensure_data() -> Result<(), String> {
    let mut guard = data_init_cell()
        .lock()
        .map_err(|_| "data_init lock poisoned".to_string())?;
    if let Some(Ok(())) = guard.as_ref() {
        return Ok(());
    }
    let res = ensure_assets().and_then(|()| {
        engine_init_data(DATA_ROOT).map_err(|e| format!("Data::init: {e}"))
    });
    *guard = Some(res.clone());
    res
}

/// 构造引擎配置：以 `ai_config.toml` 为骨架，覆盖本局马娘 / 卡组 / 种子 + 轻量 UCB 搜索组宽
fn build_config(
    uma: u32,
    cards: &[u32],
    seed: u64,
    rt_cfg: &SearchRuntimeCfg,
) -> Result<umaai::Config, String> {
    let mut cfg: umaai::Config =
        toml::from_str(AI_CFG).map_err(|e| format!("parse ai_config: {e}"))?;
    if uma > 0 {
        cfg.sim.uma = uma;
    }
    if !cards.is_empty() {
        cfg.sim.cards = cards.to_vec();
    }
    cfg.sim.seed = seed;
    cfg.sim.runs = 1;
    // 轻量 MCTS：启用 UCB 且缩小 group_size（默认 1024 会让 search_n=128 退化成均匀分配）
    cfg.search.use_ucb = true;
    cfg.search.search_group_size = rt_cfg.group_size.min(rt_cfg.search_n.max(1)).max(1);
    Ok(cfg)
}

/// 初始化决策栈（幂等：已初始化则重建 session 并换成本局身份）
pub fn init(uma: u32, cards: &[u32], seed: u64) -> Result<(), String> {
    ensure_data()?;
    let mut rt_cfg = load_search_runtime_cfg();
    let active_threads = ensure_rayon_pool(rt_cfg.threads);
    rt_cfg.threads = active_threads;

    let data =
        MatsuriData::parse(SCENARIO_ASSET).map_err(|e| format!("MatsuriData::parse: {e}"))?;
    let mut mat_cfg = MatsuriConfig::parse(MATSURI_CFG, None)
        .map_err(|e| format!("MatsuriConfig::parse: {e}"))?;

    // 挂载搜索蒸馏残差权重（对 rollout 教师与 Hand 兜底均生效）
    let distilled_loaded = match toml::from_str(DISTILLED_CFG) {
        Ok(policy) => {
            mat_cfg.distilled = Some(policy);
            true
        }
        Err(_) => false,
    };

    // 覆盖为轻量 MCTS 预算（避免 matsuri_config.toml 默认 8192 在手机/模拟器上超时）
    mat_cfg.search.search_n = rt_cfg.search_n;
    mat_cfg.search.threads = active_threads;
    mat_cfg.search.train = matches!(rt_cfg.kind, TrainerKind::Search);
    mat_cfg.search.advice = false;
    mat_cfg.search.event = false;

    // 保留本卡组对应的第 2 年 / 第 3 年教诲転成预设配对（按卡组构成自动匹配 default / stamina / wisdom / guts 档）
    let resolved_cfg = matsuri_core::deck_comp(cards)
        .map(|comp| mat_cfg.for_deck(comp))
        .unwrap_or_else(|_| mat_cfg.clone());
    let fusion_year2: Vec<(i32, i32)> = resolved_cfg
        .deck
        .fusion
        .year2
        .iter()
        .map(|p| (p.base, p.target))
        .collect();
    let fusion_year3: Vec<(i32, i32)> = resolved_cfg
        .deck
        .fusion
        .year3
        .iter()
        .map(|p| (p.base, p.target))
        .collect();

    let cfg = build_config(uma, cards, seed, &rt_cfg)?;
    let session =
        OnlineSession::new(&cfg, std::sync::Arc::new(data), mat_cfg, rt_cfg.kind, false)
            .map_err(|e| format!("OnlineSession::new: {e}"))?;
    let mut guard = cell().lock().map_err(|_| "bridge lock poisoned".to_string())?;
    *guard = Some(Bridge {
        session,
        rt_cfg,
        distilled_loaded,
        fusion_year2,
        fusion_year3,
        decided: 0,
        skipped: 0,
        failed: 0,
        advice_decided: 0,
        advice_last: String::new(),
        fusion_applied: 0,
        fusion_last: String::new(),
        event_decided: 0,
        event_last: String::new(),
        last_elapsed_ms: 0,
        last_error: String::new(),
        last: None,
    });
    Ok(())
}

/// 返回当前卡组的教诲転成配对 `[(base_id, target_id), ...]`
/// 默认（未初始化兜底）：第 2 年 `[(101,601), (201,701)]`，第 3 年 `[(501,801), (301,901), (401,1001)]`
pub fn fusion_pairs(is_year3: bool) -> Vec<(i32, i32)> {
    if let Ok(guard) = cell().lock() {
        if let Some(b) = guard.as_ref() {
            let v = if is_year3 {
                &b.fusion_year3
            } else {
                &b.fusion_year2
            };
            if !v.is_empty() {
                return v.clone();
            }
        }
    }
    if is_year3 {
        vec![(501, 801), (301, 901), (401, 1001)]
    } else {
        vec![(101, 601), (201, 701)]
    }
}

/// 记录一次教诲転成接管成功（供 /api/ai/status 观测）
pub fn record_fusion(summary: String) {
    if let Ok(mut guard) = cell().lock() {
        if let Some(b) = guard.as_mut() {
            b.fusion_applied += 1;
            b.fusion_last = summary;
        }
    }
}

/// 教诲附着决策：输入 `source="special", playing_state=51` 的帧，返回 5 条演目 `[ActType01..05]` 的目标有效教诲 ID
/// （使用 Hand+distilled 评估全部 120 种排列，单次 <3ms，不阻塞主线程）
pub fn decide_advice(frame_json: &str) -> Result<[i32; 5], String> {
    use matsuri_ai::TurnKind;
    let mut guard = cell().lock().map_err(|_| "bridge lock poisoned".to_string())?;
    let bridge = guard.as_mut().ok_or_else(|| "bridge_not_initialized".to_string())?;
    let out = bridge
        .session
        .handle_frame(frame_json)
        .map_err(|e| format!("handle_frame(advice): {e:#}"))?;
    if !matches!(out.kind, TurnKind::Decision) {
        return Err(format!(
            "advice_skipped: {}",
            out.skip_reason.unwrap_or_default()
        ));
    }
    let idx = out
        .record
        .as_ref()
        .map(|r| r.action_index)
        .ok_or_else(|| "advice_no_record".to_string())?;
    let action = out
        .actions
        .get(idx)
        .ok_or_else(|| format!("advice_index_oob:{idx}/{}", out.actions.len()))?;
    let Action::AssignAdvice(assign) = action else {
        return Err(format!("advice_unexpected_action:{:?}", action));
    };
    let ids = out.game.effective_advice_ids(assign);
    if ids.iter().any(|&x| x <= 0) {
        return Err(format!("advice_zero_id:{:?}", ids));
    }
    bridge.advice_decided += 1;
    bridge.advice_last = format!("T{}:{:?}", out.turn, ids);
    Ok(ids)
}

/// 事件选项决策：输入 `source="event", storyId=...` 的帧，返回 0-based 推荐选项下标
/// 若事件未收录或单选项跳过，返回 `Ok(None)`（调用方退回原版 omakase，零风险）
pub fn decide_event(frame_json: &str) -> Result<Option<usize>, String> {
    use matsuri_ai::TurnKind;
    let mut guard = cell().lock().map_err(|_| "bridge lock poisoned".to_string())?;
    let bridge = guard.as_mut().ok_or_else(|| "bridge_not_initialized".to_string())?;
    let out = bridge
        .session
        .handle_frame(frame_json)
        .map_err(|e| format!("handle_frame(event): {e:#}"))?;
    if !matches!(out.kind, TurnKind::Decision) {
        return Ok(None);
    }
    let Some(rec) = out.record.as_ref() else {
        return Ok(None);
    };
    let choice = rec.action_index;
    bridge.event_decided += 1;
    bridge.event_last = format!(
        "T{} sid={} -> #{} ({})",
        out.turn,
        out.story_id.unwrap_or(0),
        choice,
        rec.action_desc.replace('"', "'")
    );
    Ok(Some(choice))
}

/// 是否已初始化
pub fn ready() -> bool {
    cell().lock().map(|g| g.is_some()).unwrap_or(false)
}

fn kind_str(out: &OnlineTurnOutput) -> &'static str {
    use matsuri_ai::TurnKind;
    match out.kind {
        TurnKind::Decision => "decision",
        TurnKind::Skipped => "skip",
    }
}

/// 喂一帧（`thisTurn.json` 原文）并做本回合决策
pub fn decide(frame_json: &str) -> Result<TurnDecision, String> {
    let mut guard = cell().lock().map_err(|_| "bridge lock poisoned".to_string())?;
    let bridge = guard.as_mut().ok_or_else(|| "bridge_not_initialized".to_string())?;
    let t0 = Instant::now();
    let out = match bridge.session.handle_frame(frame_json) {
        Ok(out) => out,
        Err(e) => {
            bridge.failed += 1;
            let detail = format!("{e:#}");
            bridge.last_error = detail.clone();
            return Err(format!("handle_frame: {detail}"));
        }
    };
    let elapsed_ms = t0.elapsed().as_millis() as u64;
    let (chosen, action_desc) = match out.record.as_ref() {
        Some(r) => (r.action_index as i32, r.action_desc.clone()),
        None => (-1, String::new()),
    };

    // 候选动作 → (tag, score)：tag 带语义，hook 侧可直接按 omakase 行检索
    let mut actions: Vec<(i32, f64)> = Vec::with_capacity(out.actions.len());
    for (idx, action) in out.actions.iter().enumerate() {
        let score = out
            .candidate_scores
            .iter()
            .find(|(i, _)| *i == idx)
            .map(|(_, s)| *s)
            .unwrap_or(f64::NAN);
        actions.push((action_tag(action), score));
    }
    let chosen_tag = if chosen >= 0 {
        actions.get(chosen as usize).map(|(t, _)| *t).unwrap_or(-1)
    } else {
        -1
    };

    let decision = TurnDecision {
        turn: out.turn,
        epoch: out.epoch,
        kind: kind_str(&out),
        chosen,
        action_desc,
        candidate_n: out.actions.len(),
        search_backed: out.search_backed,
        elapsed_ms,
        note: out
            .skip_reason
            .clone()
            .or_else(|| out.notes.first().cloned())
            .unwrap_or_default(),
        scores: out.candidate_scores.clone(),
        actions,
        chosen_tag,
    };
    if decision.chosen >= 0 {
        bridge.decided += 1;
        bridge.last_elapsed_ms = elapsed_ms;
    } else {
        bridge.skipped += 1;
    }
    bridge.last = Some(decision.clone());
    Ok(decision)
}

/// 状态 JSON
pub fn status_json() -> String {
    let guard = match cell().lock() {
        Ok(g) => g,
        Err(_) => return r#"{"ok":false,"error":"lock_poisoned"}"#.to_string(),
    };
    let data_init = data_init_cell()
        .lock()
        .ok()
        .and_then(|g| g.clone())
        .map(|r| match r {
            Ok(()) => "ok".to_string(),
            Err(e) => e.replace('"', "'"),
        })
        .unwrap_or_else(|| "not_attempted".to_string());
    match guard.as_ref() {
        None => format!(
            r#"{{"ok":false,"ready":false,"data_root":"{}","data_init":"{}"}}"#,
            DATA_ROOT, data_init
        ),
        Some(b) => {
            let (turn, chosen, kind, note, desc, tag) = match &b.last {
                Some(d) => (
                    d.turn,
                    d.chosen,
                    d.kind,
                    d.note.clone(),
                    d.action_desc.clone(),
                    d.chosen_tag,
                ),
                None => (-1, -1, "none", String::new(), String::new(), -1),
            };
            let acts = b
                .last
                .as_ref()
                .map(|d| {
                    d.actions
                        .iter()
                        .map(|(t, s)| {
                            if s.is_nan() {
                                format!(r#"{{"tag":{t},"score":null}}"#)
                            } else {
                                format!(r#"{{"tag":{t},"score":{s:.2}}}"#)
                            }
                        })
                        .collect::<Vec<_>>()
                        .join(",")
                })
                .unwrap_or_default();
            let trainer_kind = match b.rt_cfg.kind {
                TrainerKind::Hand => "hand",
                TrainerKind::Search => "search",
            };
            format!(
                r#"{{"ok":true,"ready":true,"data_init":"{}","trainer":"{}","search_n":{},"group_size":{},"threads":{},"wait_ms":{},"distilled":{},"last_elapsed_ms":{},"decided":{},"skipped":{},"failed":{},"advice_decided":{},"advice_last":"{}","fusion_applied":{},"fusion_last":"{}","event_decided":{},"event_last":"{}","last_turn":{},"last_chosen":{},"last_tag":{},"last_kind":"{}","last_desc":"{}","last_note":"{}","search_backed":{},"candidate_n":{},"actions":[{}],"last_error":"{}"}}"#,
                data_init,
                trainer_kind,
                b.rt_cfg.search_n,
                b.rt_cfg.group_size,
                b.rt_cfg.threads,
                b.rt_cfg.wait_ms,
                b.distilled_loaded,
                b.last_elapsed_ms,
                b.decided,
                b.skipped,
                b.failed,
                b.advice_decided,
                b.advice_last.replace('"', "'"),
                b.fusion_applied,
                b.fusion_last.replace('"', "'"),
                b.event_decided,
                b.event_last.replace('"', "'"),
                turn,
                chosen,
                tag,
                kind,
                desc.replace('"', "'"),
                note.replace('"', "'"),
                b.last.as_ref().map(|d| d.search_backed).unwrap_or(false),
                b.last.as_ref().map(|d| d.candidate_n).unwrap_or(0),
                acts,
                b.last_error.replace('"', "'"),
            )
        }
    }
}
