//! matsuri 手写档打分器（Scorer 实现）
//!
//! 权重全部来自 `umaai-matsuri` 生产配置 `data/matsuri_config.toml` 的
//! `[decks.default.trainer]`（2026-11-09 GA run2 冠军 g0269 定档）。
//! 这里只做**通用层**：五维偏好 + 体力/干劲/羁绊/hint 折算 + PT 偏好 + 休息阈值。
//! 祭典专属（gauge / act_level / checkpoint）只在 scenario 15 生效，见 `festival_layer`。
//!
//! 设计约束：
//!   * 纯函数，不读全局、不写全局 —— 决策线程可并发调用；
//!   * 任何输入异常返回 None（调用方回退原生权重），绝不 panic；
//!   * 输出单位 = 评分点，与 matsuri 离线口径一致，便于逐回合比对。

use crate::decisor_async::{Scorer, Snapshot};

/// `[decks.default.trainer]` 的完整旋钮
#[derive(Clone, Debug)]
pub struct MatsuriWeights {
    /// 速 / 耐 / 力 / 根 / 智
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

impl Default for MatsuriWeights {
    fn default() -> Self {
        // 值 = data/matsuri_config.toml [decks.default.trainer]，逐位抄录
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
}

/// 候选顺序：速 耐 力 根 智 休息 外出
pub const CAND_SPEED: usize = 0;
pub const CAND_STAMINA: usize = 1;
pub const CAND_POWER: usize = 2;
pub const CAND_GUTS: usize = 3;
pub const CAND_WIT: usize = 4;
pub const CAND_REST: usize = 5;
pub const CAND_OUTING: usize = 6;

/// 一次训练的直接收益（与 /debug/training_log、/summary 同口径）
fn train_score(w: &MatsuriWeights, i: usize, snap: &Snapshot) -> f64 {
    let main = f64::from(snap.gains[i][0]);
    let sub = f64::from(snap.gains[i][1]);
    let base = w.status_weights[i] * (main + 0.5 * sub);

    // 失败率把收益按期望打折（失败 = 收益减半 + 体力损失）
    let fail = f64::from(snap.stamina_penalty.clamp(0.0, 1.0));
    let risk = 1.0 - fail * 0.5;

    // 羁绊：本项训练参与伙伴的羁绊增量总和
    let friend_gain: i32 = snap
        .partners
        .iter()
        .filter(|(pid, _)| (*pid & 0x0F) == i as i32)
        .map(|(_, bond)| *bond)
        .sum();
    let friend_term = w.friendship * f64::from(friend_gain);

    // hint 概率、PT
    let hint_term = w.hint * f64::from(snap.hint);
    let pt_term = w.pt_favor_rate * f64::from(snap.pt.max(0));

    (base + friend_term + hint_term + pt_term) * risk
}

/// 休息：按体力缺口线性估值（体力越低越值）
fn rest_score(w: &MatsuriWeights, snap: &Snapshot) -> f64 {
    let gap = (snap.max_vital - snap.vital).max(0);
    if gap == 0 {
        return 0.0;
    }
    let mut v = w.vital * f64::from(gap);
    // 低于 rest_vital 阈值时额外加权（matsuri 手写档的"该休息了"区间）
    if snap.vital < w.rest_vital {
        v += f64::from(w.rest_vital - snap.vital) * w.vital * 2.0;
    }
    v
}

/// 外出：基础收益 + 友人外出尽早用完（friend_outing_early = true）
fn outing_score(w: &MatsuriWeights, snap: &Snapshot) -> f64 {
    let mut v = w.vital * 3.0; // 外出 = 固定体力恢复 + 事件
    if snap.vital < snap.max_vital / 2 {
        v += 120.0; // FRIEND_OUTING_EARLY_BONUS 同口径
    }
    v
}

/// 祭典专属层（scenario 15）：演目 Lv / gauge / 発表会 checkpoint
/// 非 scenario 15 时 `festival_scale = 0`，本层整体不生效。
fn festival_layer(w: &MatsuriWeights, snap: &Snapshot, festival_scale: f64) -> f64 {
    if festival_scale <= 0.0 {
        return 0.0;
    }
    let gauge_term = w.gauge * f64::from(snap.gauge);
    let act_term = w.act_level * f64::from(snap.gauge * 0.1); // 演目等级随 gauge 推进的近似
    (gauge_term + act_term) * festival_scale
}

pub struct MatsuriScorer {
    pub weights: MatsuriWeights,
    /// 1.0 = 祭典剧本（scenario 15）；0.0 = 其他剧本，专属层关闭
    pub festival_scale: f64,
}

impl Default for MatsuriScorer {
    fn default() -> Self {
        Self { weights: MatsuriWeights::default(), festival_scale: 1.0 }
    }
}

impl Scorer for MatsuriScorer {
    fn score(&self, snap: &Snapshot) -> Option<[f64; 7]> {
        // 输入健全性：任何越界/NaN 直接返回 None，让调用方回退
        if snap.status.iter().any(|v| *v < 0) || snap.max_vital <= 0 {
            return None;
        }
        if snap.stamina_penalty.is_nan() || snap.hint.is_nan() || snap.gauge.is_nan() {
            return None;
        }
        let w = &self.weights;
        let mut s = [0f64; 7];
        for i in 0..5 {
            s[i] = train_score(w, i, snap) + festival_layer(w, snap, self.festival_scale);
        }
        s[CAND_REST] = rest_score(w, snap);
        s[CAND_OUTING] = outing_score(w, snap);
        if s.iter().any(|v| !v.is_finite()) {
            return None;
        }
        Some(s)
    }
}

/// 把 7 个分数编码成游戏侧 baseWeight（原生量级 10~200，我们给 200~1000）
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
    fn speed_wins_in_default_deck() {
        let sc = MatsuriScorer { weights: MatsuriWeights::default(), festival_scale: 0.0 };
        let s = sc.score(&snap()).unwrap();
        let best = (0..7).max_by(|a, b| s[*a].partial_cmp(&s[*b]).unwrap()).unwrap();
        assert_eq!(best, CAND_SPEED);
    }

    #[test]
    fn low_vital_prefers_rest() {
        let mut sp = snap();
        sp.vital = 5;
        sp.gains = [[1, 0]; 5];
        let sc = MatsuriScorer { weights: MatsuriWeights::default(), festival_scale: 0.0 };
        let s = sc.score(&sp).unwrap();
        assert!(s[CAND_REST] > s[CAND_SPEED]);
    }

    #[test]
    fn encode_is_bounded() {
        let mut v = [0f64; 7];
        v[3] = 1.0;
        let w = encode_base_weight(&v, 3);
        assert!((0..=1000).contains(&w));
    }
}
