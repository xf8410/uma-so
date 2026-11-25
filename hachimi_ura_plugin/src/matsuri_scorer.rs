//! matsuri 打分器（两套卡组 + 可热调 PT 权重）
//!
//! 权重来源：`umaai-matsuri/data/matsuri_config.toml` 的 `decks.default`（速主）
//! 与 `decks.wisdom_build`（智主双智），逐位抄录。
//!
//! 运行时覆盖（不改二进制、不重装 APK）：
//!   `/sdcard/Download/uma_so/pt_favor_rate.txt`  内容一个浮点数，例如 `2.5`
//!   `/sdcard/Download/uma_so/deck.txt`           内容 `0`（速主 211011）或 `1`（智主 111021）
//! 决策线程在每回合打分前读一次（带 2 秒缓存），hook 路径不碰 IO。

use crate::decisor_async::{Scorer, Snapshot};
use std::time::{Duration, Instant};

pub const CFG_DIR: &str = "/sdcard/Download/uma_so";
pub const CFG_PT: &str = "/sdcard/Download/uma_so/pt_favor_rate.txt";
pub const CFG_DECK: &str = "/sdcard/Download/uma_so/deck.txt";

#[derive(Clone, Debug)]
pub struct MatsuriWeights {
    pub status_weights: [f64; 5],
    pub vital: f64,
    pub motivation: f64,
    pub friendship: f64,
    pub hint: f64,
    pub gauge: f64,
    pub act_level: f64,
    pub checkpoint: f64,
    pub fat: f64,
    pub pt_favor_rate: f64,
    pub rest_vital: i32,
}

impl MatsuriWeights {
    /// 211011 速主 = decks.default
    pub fn speed() -> Self {
        Self {
            status_weights: [0.994888, 0.542242, 0.407084, 0.156003, 0.860951],
            vital: 5.730157,
            motivation: 10.93116,
            friendship: 3.339714,
            hint: 4.313881,
            gauge: 53.094769,
            act_level: 58.536224,
            checkpoint: 2223.151961,
            fat: 193.041812,
            pt_favor_rate: 1.931954,
            rest_vital: 66,
        }
    }

    /// 111021 智主双智 = decks.wisdom_build
    pub fn wisdom() -> Self {
        Self {
            status_weights: [0.793, 0.845, 0.694, 0.147, 0.025],
            vital: 6.394,
            motivation: 8.073,
            friendship: 11.736,
            hint: 21.666,
            gauge: 58.381,
            act_level: 67.01,
            checkpoint: 959.3,
            fat: 284.5,
            pt_favor_rate: 1.536833,
            rest_vital: 84,
        }
    }

    pub fn by_deck(deck: usize) -> Self {
        match deck {
            1 => Self::wisdom(),
            _ => Self::speed(),
        }
    }
}

impl Default for MatsuriWeights {
    fn default() -> Self {
        Self::speed()
    }
}

pub const CAND_SPEED: usize = 0;
pub const CAND_STAMINA: usize = 1;
pub const CAND_POWER: usize = 2;
pub const CAND_GUTS: usize = 3;
pub const CAND_WIT: usize = 4;
pub const CAND_REST: usize = 5;
pub const CAND_OUTING: usize = 6;

/// 读文本配置（失败返回 None，调用方用默认值）
fn read_float(path: &str) -> Option<f64> {
    let s = std::fs::read_to_string(path).ok()?;
    s.trim().parse::<f64>().ok().filter(|v| v.is_finite() && *v >= 0.0 && *v <= 20.0)
}

fn read_usize(path: &str) -> Option<usize> {
    let s = std::fs::read_to_string(path).ok()?;
    s.trim().parse::<usize>().ok().filter(|v| *v <= 3)
}

struct CfgCache {
    at: Option<Instant>,
    pt_override: Option<f64>,
    deck: usize,
}

pub struct MatsuriScorer {
    pub festival_scale: f64,
    cache: std::sync::Mutex<CfgCache>,
}

impl Default for MatsuriScorer {
    fn default() -> Self {
        Self {
            festival_scale: 1.0,
            cache: std::sync::Mutex::new(CfgCache { at: None, pt_override: None, deck: 0 }),
        }
    }
}

impl MatsuriScorer {
    /// 2 秒缓存：避免每回合多次 IO
    fn current(&self) -> (MatsuriWeights, f64) {
        let mut c = match self.cache.lock() {
            Ok(g) => g,
            Err(p) => p.into_inner(),
        };
        let fresh = c
            .at
            .map(|t| t.elapsed() < Duration::from_secs(2))
            .unwrap_or(false);
        if !fresh {
            c.deck = read_usize(CFG_DECK).unwrap_or(c.deck);
            c.pt_override = read_float(CFG_PT);
            c.at = Some(Instant::now());
        }
        let mut w = MatsuriWeights::by_deck(c.deck);
        if let Some(pt) = c.pt_override {
            w.pt_favor_rate = pt;
        }
        (w, self.festival_scale)
    }
}

fn train_score(w: &MatsuriWeights, i: usize, snap: &Snapshot) -> f64 {
    let main = f64::from(snap.gains[i][0]);
    let sub = f64::from(snap.gains[i][1]);
    let base = w.status_weights[i] * (main + 0.5 * sub);
    let fail = f64::from(snap.stamina_penalty.clamp(0.0, 1.0));
    let risk = 1.0 - fail * 0.5;
    let friend_gain: i32 = snap
        .partners
        .iter()
        .filter(|(pid, _)| (*pid & 0x0F) == i as i32)
        .map(|(_, bond)| *bond)
        .sum();
    let friend_term = w.friendship * f64::from(friend_gain);
    let hint_term = w.hint * f64::from(snap.hint);
    let pt_term = w.pt_favor_rate * f64::from(snap.pt.max(0));
    (base + friend_term + hint_term + pt_term) * risk
}

fn rest_score(w: &MatsuriWeights, snap: &Snapshot) -> f64 {
    let gap = (snap.max_vital - snap.vital).max(0);
    if gap == 0 {
        return 0.0;
    }
    let mut v = w.vital * f64::from(gap);
    if snap.vital < w.rest_vital {
        v += f64::from(w.rest_vital - snap.vital) * w.vital * 2.0;
    }
    v
}

fn outing_score(w: &MatsuriWeights, snap: &Snapshot) -> f64 {
    let mut v = w.vital * 3.0;
    if snap.vital < snap.max_vital / 2 {
        v += 120.0;
    }
    v
}

fn festival_layer(w: &MatsuriWeights, snap: &Snapshot, scale: f64) -> f64 {
    if scale <= 0.0 {
        return 0.0;
    }
    let gauge_term = w.gauge * f64::from(snap.gauge);
    let act_term = w.act_level * f64::from(snap.gauge * 0.1);
    (gauge_term + act_term) * scale
}

impl Scorer for MatsuriScorer {
    fn score(&self, snap: &Snapshot) -> Option<[f64; 7]> {
        if snap.status.iter().any(|v| *v < 0) || snap.max_vital <= 0 {
            return None;
        }
        if snap.stamina_penalty.is_nan() || snap.hint.is_nan() || snap.gauge.is_nan() {
            return None;
        }
        let (w, scale) = self.current();
        let mut s = [0f64; 7];
        for i in 0..5 {
            s[i] = train_score(&w, i, snap) + festival_layer(&w, snap, scale);
        }
        s[CAND_REST] = rest_score(&w, snap);
        s[CAND_OUTING] = outing_score(&w, snap);
        if s.iter().any(|v| !v.is_finite()) {
            return None;
        }
        Some(s)
    }
}

/// 7 个分数 → 游戏 baseWeight（原生量级 10~200，我们给 200~1000）
pub fn encode_base_weight(scores: &[f64; 7], chosen: usize) -> i32 {
    if scores.iter().any(|v| !v.is_finite()) {
        return 0;
    }
    let max = scores.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let min = scores.iter().cloned().fold(f64::INFINITY, f64::min);
    let span = (max - min).max(1e-6);
    let v = scores.get(chosen).copied().unwrap_or(max);
    ((200.0 + 800.0 * (v - min) / span).round() as i32).clamp(0, 1000)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn snap() -> Snapshot {
        Snapshot {
            turn: 10,
            vital: 40,
            max_vital: 100,
            motivation: 2,
            status: [300, 200, 200, 150, 250],
            gains: [[20, 5], [10, 3], [12, 2], [8, 1], [15, 4]],
            partners: vec![(101001, 2), (101002, 1), (102001, 2), (103001, 1), (105001, 3)],
            hint: 0.1,
            gauge: 0.4,
            pt: 30,
            stamina_penalty: 0.15,
            turn_total: 78,
        }
    }

    #[test]
    fn speed_deck_prefers_speed() {
        let sc = MatsuriScorer { festival_scale: 0.0, ..Default::default() };
        let mut sp = snap();
        // 避免读设备文件影响单测：默认文件不存在 → 用默认 deck
        sp.gains = [[30, 0], [5, 0], [5, 0], [5, 0], [5, 0]];
        let s = sc.score(&sp).unwrap();
        let best = (0..7).max_by(|a, b| s[*a].partial_cmp(&s[*b]).unwrap()).unwrap();
        assert_eq!(best, CAND_SPEED);
    }

    #[test]
    fn wisdom_deck_boosts_pt_and_hint() {
        let speed = MatsuriWeights::speed();
        let wisdom = MatsuriWeights::wisdom();
        assert!(wisdom.pt_favor_rate < speed.pt_favor_rate || wisdom.hint > speed.hint);
        assert!(wisdom.hint > speed.hint);
        assert!(wisdom.friendship > speed.friendship);
    }

    #[test]
    fn encode_bounded() {
        let mut v = [0f64; 7];
        v[2] = 1.0;
        assert!((0..=1000).contains(&encode_base_weight(&v, 2)));
    }
}
