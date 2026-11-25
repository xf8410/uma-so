//! 自研比赛过滤器（不依赖游戏 omakase 的 filter 表）
//!
//! 为什么自己写：游戏 filter 只能表达「按等级/距离/场地粗筛」，无法表达
//! 「这匹马适性够不够」。而 matsuri 的策略要求按马娘适性选赛，否则会出现
//! 牝马去打牡马限定、短距离马去打 2400m 这种低级失败。
//!
//! 数据来源（全部明文，可核对）：
//!   * 适性：`WorkSingleModeCharaData.get_ProperDistance{Short,Mile,Middle,Long}` /
//!           `get_ProperGround{Turf,Dirt}` / `get_ProperRunningStyle{Nige,Senko,Sashi,Oikomi}`
//!           → 返回 `Gallop.RaceDefine.ProperGrade`
//!   * 赛程：`single_mode_program`（month, half, race_instance_id, need_fan_count, filly_only_flag）
//!   * 路线：`race`（grade, course_set）→ `race_course_set`（distance, ground）
//!
//! 阈值全部可配，默认「适性 C 及以上才打」，与手写档保守口径一致。

/// 距离档（赛马娘标准切分）
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum DistanceType {
    Short,  // <= 1400
    Mile,   // 1401..=1800
    Middle, // 1801..=2400
    Long,   // > 2400
}

pub fn distance_type(meters: i32) -> DistanceType {
    match meters {
        m if m <= 1400 => DistanceType::Short,
        m if m <= 1800 => DistanceType::Mile,
        m if m <= 2400 => DistanceType::Middle,
        _ => DistanceType::Long,
    }
}

/// 场地（与 `race_course_set.ground` 对齐：1=芝，2=ダート）
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Ground {
    Turf,
    Dirt,
    Unknown,
}

pub fn ground_from_code(code: i32) -> Ground {
    match code {
        1 => Ground::Turf,
        2 => Ground::Dirt,
        _ => Ground::Unknown,
    }
}

/// 适性等级换算：游戏 `ProperGrade` 越大越差（S=0? 以实测为准），
/// 这里统一成「0 = 最好、6 = 最差」的可比较分，调用方用 `/il2cpp/invoke` 读回原值即可。
pub type GradeScore = i32;

/// 马娘一侧的适性快照
#[derive(Clone, Copy, Debug)]
pub struct Aptitude {
    pub dist: [GradeScore; 4],    // short/mile/middle/long
    pub ground: [GradeScore; 2],  // turf/dirt
    pub style: [GradeScore; 4],   // nige/senko/sashi/oikomi
    pub running_style: usize,     // 当前跑法 0..=3
    pub is_filly: bool,           // 牝马
}

/// 一匹候选比赛
#[derive(Clone, Copy, Debug)]
pub struct RaceCandidate {
    pub instance_id: i32,
    pub month: i32,
    pub half: i32,
    pub need_fan: i32,
    pub filly_only: bool,
    pub grade: i32,      // 100=G1 200=G2 300=G3 400=OP 500=Pre-OP …
    pub distance: i32,   // 米
    pub ground: Ground,
    pub is_target: bool, // 生涯目标赛（必须打）
}

/// 过滤配置
#[derive(Clone, Copy, Debug)]
pub struct FilterConfig {
    /// 适性门槛（0 最好）：超过这个等级视为"不适配"
    pub max_grade: GradeScore,
    /// 非目标赛也允许的距离适性放宽（true = 只看场地/跑法）
    pub allow_awkward_distance: bool,
    /// 粉丝门槛是否硬性生效
    pub require_fan: bool,
}

impl Default for FilterConfig {
    fn default() -> Self {
        // 默认：适性 <= 2（约 C 以上）、粉丝硬性、距离必须达标
        Self { max_grade: 2, allow_awkward_distance: false, require_fan: true }
    }
}

/// 该比赛是否可打
pub fn eligible(apt: &Aptitude, race: &RaceCandidate, fan: i32, cfg: &FilterConfig) -> bool {
    if race.is_target {
        return true; // 目标赛无条件
    }
    if cfg.require_fan && fan < race.need_fan {
        return false;
    }
    if race.filly_only && !apt.is_filly {
        return false;
    }
    // 场地适性
    let g = match race.ground {
        Ground::Turf => apt.ground[0],
        Ground::Dirt => apt.ground[1],
        Ground::Unknown => 0,
    };
    if g > cfg.max_grade {
        return false;
    }
    // 距离适性
    if !cfg.allow_awkward_distance {
        let d = match distance_type(race.distance) {
            DistanceType::Short => apt.dist[0],
            DistanceType::Mile => apt.dist[1],
            DistanceType::Middle => apt.dist[2],
            DistanceType::Long => apt.dist[3],
        };
        if d > cfg.max_grade {
            return false;
        }
    }
    // 跑法适性
    if apt.running_style < 4 && apt.style[apt.running_style] > cfg.max_grade {
        return false;
    }
    true
}

/// 打分：等级越高越好、适性越好越好、粉丝需求越低越好
pub fn score(apt: &Aptitude, race: &RaceCandidate, fan: i32) -> f64 {
    let grade_val = match race.grade {
        g if g <= 100 => 5.0,  // G1
        g if g <= 200 => 4.0,  // G2
        g if g <= 300 => 3.0,  // G3
        g if g <= 400 => 2.0,  // OP
        _ => 1.0,
    };
    let dist = match distance_type(race.distance) {
        DistanceType::Short => apt.dist[0],
        DistanceType::Mile => apt.dist[1],
        DistanceType::Middle => apt.dist[2],
        DistanceType::Long => apt.dist[3],
    };
    let ground = match race.ground {
        Ground::Turf => apt.ground[0],
        Ground::Dirt => apt.ground[1],
        Ground::Unknown => 0,
    };
    let apt_bonus = (6.0 - f64::from(dist.max(0))) + (6.0 - f64::from(ground.max(0)));
    let fan_bonus = if race.need_fan > 0 {
        (f64::from(fan) / f64::from(race.need_fan)).min(3.0)
    } else {
        3.0
    };
    grade_val * 100.0 + apt_bonus * 10.0 + fan_bonus
}

/// 从候选里挑一场（目标赛优先，其次分数最高）
pub fn pick(apt: &Aptitude, races: &[RaceCandidate], fan: i32, cfg: &FilterConfig) -> Option<usize> {
    let mut best: Option<(usize, f64)> = None;
    for (i, r) in races.iter().enumerate() {
        if !eligible(apt, r, fan, cfg) {
            continue;
        }
        let s = if r.is_target { f64::MAX } else { score(apt, r, fan) };
        if best.map(|(_, bs)| s > bs).unwrap_or(true) {
            best = Some((i, s));
        }
    }
    best.map(|(i, _)| i)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn apt_filly_sprinter() -> Aptitude {
        Aptitude {
            dist: [0, 3, 5, 6],
            ground: [0, 5],
            style: [1, 0, 4, 5],
            running_style: 1,
            is_filly: true,
        }
    }

    fn race(distance: i32, ground: Ground, filly_only: bool) -> RaceCandidate {
        RaceCandidate {
            instance_id: 1, month: 6, half: 1, need_fan: 0,
            filly_only, grade: 100, distance, ground, is_target: false,
        }
    }

    #[test]
    fn rejects_long_race_for_sprinter() {
        let cfg = FilterConfig::default();
        let a = apt_filly_sprinter();
        assert!(eligible(&a, &race(1200, Ground::Turf, false), 10000, &cfg));
        assert!(!eligible(&a, &race(3000, Ground::Turf, false), 10000, &cfg));
    }

    #[test]
    fn rejects_filly_only_for_colt() {
        let cfg = FilterConfig::default();
        let mut a = apt_filly_sprinter();
        a.is_filly = false;
        assert!(!eligible(&a, &race(1200, Ground::Turf, true), 10000, &cfg));
    }

    #[test]
    fn rejects_insufficient_fans() {
        let cfg = FilterConfig::default();
        let a = apt_filly_sprinter();
        let mut r = race(1200, Ground::Turf, false);
        r.need_fan = 50000;
        assert!(!eligible(&a, &r, 1000, &cfg));
        assert!(eligible(&a, &r, 60000, &cfg));
    }

    #[test]
    fn dirt_unfit_rejected() {
        let cfg = FilterConfig::default();
        let a = apt_filly_sprinter();
        assert!(!eligible(&a, &race(1200, Ground::Dirt, false), 10000, &cfg));
    }
}
