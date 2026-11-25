//! 异步决策器：主线程零阻塞，后台线程算 matsuri 分值。
//!
//! 设计约束（来自真机经验）：
//!   1. hook 回调里禁止任何可能阻塞的操作（v3.24.52 开机 ANR 的教训）；
//!   2. 决策必须与"当前回合"严格对齐，跨回合的旧结果一律丢弃；
//!   3. 任何异常路径都回退原生权重，绝不返回半成品。
//!
//! 线程模型：
//!   hook 线程 --snapshot--> 有界通道(容量1,覆盖式) --> 决策线程(可跑 100~800ms)
//!        ^                                                    |
//!        +----------- ArcSwap<Option<Decision>> <-------------+
//!
//! 取结果用 lock-free 读（Arc + Mutex 短临界区即可，读侧只 clone 一个小结构）。

use std::sync::atomic::{AtomicU64, AtomicBool, Ordering};
use std::sync::mpsc::{sync_channel, SyncSender, TrySendError, Receiver};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

/// 一个回合的决策快照。字段与 /summary、/debug/training_log 已解析的结构一致。
#[derive(Clone, Debug, Default)]
pub struct Snapshot {
    pub turn: u32,
    pub vital: i32,
    pub max_vital: i32,
    pub motivation: i32,
    pub status: [i32; 5],        // 速 耐 力 根 智
    pub gains: [[i32; 2]; 5],    // 每项 [主收益, 副收益]
    pub partners: Vec<(i32, i32)>, // (partner_id, 羁绊)
    pub hint: f32,
    pub gauge: f32,
    pub pt: i32,
    pub stamina_penalty: f32,    // 失败率
    pub turn_total: u32,
}

/// 决策结果：回合内 7 个候选的分数 + 选中的命令序号。
#[derive(Clone, Debug, Default)]
pub struct Decision {
    pub turn: u32,
    pub scores: [f64; 7],   // speed/stamina/power/guts/wit/rest/outing
    pub chosen: usize,
    pub elapsed_ms: u64,
    pub source: &'static str,
}

pub trait Scorer: Send + Sync + 'static {
    /// 纯函数：给定快照返回 7 个候选分值。允许耗时，不允许阻塞在锁上。
    fn score(&self, snap: &Snapshot) -> Option<[f64; 7]>;
}

pub struct AsyncDecider {
    tx: SyncSender<Snapshot>,
    latest: Arc<Mutex<Option<Decision>>>,
    epoch: Arc<AtomicU64>,
    running: Arc<AtomicBool>,
    pub computed: Arc<AtomicU64>,
    pub missed: Arc<AtomicU64>,   // 到决策时还没算完的次数
    pub rejected: Arc<AtomicU64>, // 回合不匹配被丢弃的次数
    pub dropped: Arc<AtomicU64>,  // 通道满导致丢弃的次数
}

impl AsyncDecider {
    pub fn start<S: Scorer>(scorer: S) -> Self {
        let (tx, rx): (SyncSender<Snapshot>, Receiver<Snapshot>) = sync_channel(1);
        let latest = Arc::new(Mutex::new(None));
        let epoch = Arc::new(AtomicU64::new(0));
        let running = Arc::new(AtomicBool::new(true));
        let computed = Arc::new(AtomicU64::new(0));
        let missed = Arc::new(AtomicU64::new(0));
        let rejected = Arc::new(AtomicU64::new(0));
        let dropped = Arc::new(AtomicU64::new(0));

        let (l2, e2, r2, c2) = (latest.clone(), epoch.clone(), running.clone(), computed.clone());
        thread::Builder::new()
            .name("matsuri-decide".into())
            .stack_size(8 * 1024 * 1024)
            .spawn(move || {
                while r2.load(Ordering::Relaxed) {
                    let snap = match rx.recv_timeout(Duration::from_millis(200)) {
                        Ok(s) => s,
                        Err(_) => continue,
                    };
                    let t0 = Instant::now();
                    let my_epoch = e2.load(Ordering::Relaxed);
                    let scores = match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| scorer.score(&snap))) {
                        Ok(Some(s)) => s,
                        _ => continue,
                    };
                    // 算完之后如果 epoch 已经翻页（玩家已进下一回合），直接丢弃
                    if e2.load(Ordering::Relaxed) != my_epoch {
                        continue;
                    }
                    let mut chosen = 0usize;
                    for i in 1..7 {
                        if scores[i] > scores[chosen] { chosen = i; }
                    }
                    *l2.lock().unwrap() = Some(Decision {
                        turn: snap.turn,
                        scores,
                        chosen,
                        elapsed_ms: t0.elapsed().as_millis() as u64,
                        source: "matsuri",
                    });
                    c2.fetch_add(1, Ordering::Relaxed);
                }
            })
            .expect("spawn matsuri-decide");

        Self { tx, latest, epoch, running, computed, missed, rejected, dropped }
    }

    /// 主线程调用：提交快照。绝不阻塞 —— 通道满就覆盖（丢掉来不及算的那份）。
    pub fn submit(&self, snap: Snapshot) {
        self.epoch.fetch_add(1, Ordering::Relaxed);
        match self.tx.try_send(snap) {
            Ok(()) => {}
            Err(TrySendError::Full(_)) => { self.dropped.fetch_add(1, Ordering::Relaxed); } // 来不及算就丢，下一回合再提交
            Err(TrySendError::Disconnected(_)) => {}
        }
    }

    /// hook 内调用：取当前回合的结果。没有就返回 None，调用方回退原生权重。
    pub fn take_for(&self, turn: u32) -> Option<Decision> {
        let d = self.latest.lock().ok()?.clone()?;
        if d.turn != turn {
            self.rejected.fetch_add(1, Ordering::Relaxed);
            return None;
        }
        Some(d)
    }

    pub fn note_miss(&self) { self.missed.fetch_add(1, Ordering::Relaxed); }

    pub fn status_json(&self) -> String {
        let d = self.latest.lock().ok().and_then(|g| g.clone());
        let (turn, ms, chosen, src) = match d {
            Some(d) => (d.turn, d.elapsed_ms, d.chosen, d.source),
            None => (0, 0, 0, "none"),
        };
        format!(
            "{{\"ok\":true,\"turn\":{},\"elapsed_ms\":{},\"chosen\":{},\"source\":\"{}\",\"computed\":{},\"missed\":{},\"rejected\":{},\"dropped\":{}}}",
            turn, ms, chosen, src,
            self.computed.load(Ordering::Relaxed),
            self.missed.load(Ordering::Relaxed),
            self.rejected.load(Ordering::Relaxed),
            self.dropped.load(Ordering::Relaxed),
        )
    }
}

impl Drop for AsyncDecider {
    fn drop(&mut self) { self.running.store(false, Ordering::Relaxed); }
}
