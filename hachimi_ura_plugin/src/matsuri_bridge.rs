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

use matsuri_ai::session::{ensure_data as engine_init_data, OnlineSession, TrainerKind};
use matsuri_ai::OnlineTurnOutput;
use matsuri_core::config::MatsuriConfig;
use matsuri_core::data::MatsuriData;
use matsuri_core::game::Action;

/// 运行时数据根（引擎 `Data::init` 的入参；其下需有 6 张 gamedata 表）
pub const DATA_ROOT: &str = "/sdcard/Download/uma_so/matsuri_data";
/// 剧本资产文件（`MatsuriData::parse` 的输入，随 so 内嵌）
const SCENARIO_ASSET: &str = include_str!("../../vendor/matsuri/data/scenario_matsuri.json");
/// 剧本配置（融合预设 / 卡组档位，随 so 内嵌）
const MATSURI_CFG: &str = include_str!("../../vendor/matsuri/data/matsuri_config.toml");
/// 引擎配置模板（sim / search / record / output；uma / cards / seed 运行时覆盖）
const AI_CFG: &str = include_str!("../../vendor/matsuri/data/ai_config.toml");

// ===== 动作标记 =====
//
// 把 matsuri 的 `Action` 压成一个稳定的 i32，供 hook 侧按 omakase 行检索。
//
// omakase 编号（实测锁定，游戏 2.33.2 / setting 1501）：
//   150101..150105 = Train(0..4)     速 耐 力 根 智（与 training.normal[] 顺序一致）
//   150120..150124 = ActTrain(0..4)  演目下标（与 scenario_matsuri.json acts[].train 一致）
// 其余 150106 / 150107..150110 是休息/外出/比赛一类，暂不接管。
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
    decided: u64,
    skipped: u64,
    failed: u64,
    last_error: String,
    last: Option<TurnDecision>,
}

static BRIDGE: OnceLock<Mutex<Option<Bridge>>> = OnceLock::new();
static DATA_INIT: OnceLock<Result<(), String>> = OnceLock::new();

fn cell() -> &'static Mutex<Option<Bridge>> {
    BRIDGE.get_or_init(|| Mutex::new(None))
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

/// 把内嵌资产展开到运行时目录（已存在不覆盖）
fn ensure_assets() -> Result<(), String> {
    let root = Path::new(DATA_ROOT);
    if root.join("constants.json").exists() {
        return Ok(());
    }
    std::fs::create_dir_all(root).map_err(|e| format!("mkdir {DATA_ROOT}: {e}"))?;
    for (name, body) in EMBEDDED_GAMEDATA {
        let path = root.join(name);
        if path.exists() {
            continue;
        }
        std::fs::write(&path, body).map_err(|e| format!("write {}: {e}", path.display()))?;
    }
    Ok(())
}

/// 引擎全局数据初始化（进程内只尝试一次）
fn ensure_data() -> Result<(), String> {
    DATA_INIT
        .get_or_init(|| {
            ensure_assets()?;
            engine_init_data(DATA_ROOT).map_err(|e| format!("Data::init: {e}"))
        })
        .clone()
}

/// 构造引擎配置：以 `ai_config.toml` 为骨架，覆盖本局马娘 / 卡组 / 种子
fn build_config(uma: u32, cards: &[u32], seed: u64) -> Result<umaai::Config, String> {
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
    Ok(cfg)
}

/// 初始化决策栈（幂等：已初始化则重建 session 并换成本局身份）
pub fn init(uma: u32, cards: &[u32], seed: u64) -> Result<(), String> {
    ensure_data()?;
    let data =
        MatsuriData::parse(SCENARIO_ASSET).map_err(|e| format!("MatsuriData::parse: {e}"))?;
    let mat_cfg: MatsuriConfig =
        toml::from_str(MATSURI_CFG).map_err(|e| format!("parse matsuri_config: {e}"))?;
    let cfg = build_config(uma, cards, seed)?;
    let session =
        OnlineSession::new(&cfg, std::sync::Arc::new(data), mat_cfg, TrainerKind::Hand, false)
            .map_err(|e| format!("OnlineSession::new: {e}"))?;
    let mut guard = cell().lock().map_err(|_| "bridge lock poisoned".to_string())?;
    *guard = Some(Bridge {
        session,
        decided: 0,
        skipped: 0,
        failed: 0,
        last_error: String::new(),
        last: None,
    });
    Ok(())
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
    let out = match bridge.session.handle_frame(frame_json) {
        Ok(out) => out,
        Err(e) => {
            bridge.failed += 1;
            bridge.last_error = e.to_string();
            return Err(format!("handle_frame: {e}"));
        }
    };
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
    let data_init = DATA_INIT
        .get()
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
            format!(
                r#"{{"ok":true,"ready":true,"data_init":"{}","decided":{},"skipped":{},"failed":{},"last_turn":{},"last_chosen":{},"last_tag":{},"last_kind":"{}","last_desc":"{}","last_note":"{}","search_backed":{},"candidate_n":{},"actions":[{}],"last_error":"{}"}}"#,
                data_init,
                b.decided,
                b.skipped,
                b.failed,
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