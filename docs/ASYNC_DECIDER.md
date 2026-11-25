# 异步决策器（decisor_async）

## 为什么必须异步

- 单次搜索决策 8192 rollout 实测在 PC 上 100~600 ms，手机只会更慢；放进 il2cpp hook 回调 = 直接卡帧。
- v3.24.52 那次**开机 ANR** 就是同类错误：在初始化线程里跑了无超时的阻塞网络请求。凡是 hook 回调里
  出现"等结果"，一律按事故处理。
- 游戏主线程每回合只等 1 帧（16.7 ms），我们给决策线程的预算是 **整个回合间隔**（玩家看动画那几秒）。

## 数据流

```
hook: SelectTraining
  ├─ 读快照（turn/vital/motivation/status/gains/partners/hint/gauge/pt/失败率）
  ├─ decider.submit(snapshot)        ← 只做 try_send，永不阻塞
  └─ decider.take_for(turn)          ← 立即返回
        ├─ Some(d) → 把 d.scores 编码成 baseWeight 返回
        └─ None    → 原值返回（回退），并 note_miss()

decide 线程（1 个，8MB 栈）
  └─ scorer.score(&snap) → [f64;7] → 写 latest（带 turn + epoch 校验）
```

## 三条铁律

1. **回合严格相等**：`take_for(turn)` 只在 `d.turn == 当前 turn` 时返回，否则丢弃并计入 `rejected`。
2. **epoch 翻页即弃**：结果算完时若 epoch 已变（玩家已进下一回合），直接丢掉，不写 latest。
3. **全路径回退**：`catch_unwind` 包住 scorer，panic / None / 超时一律回退原生权重。

## 性能预算

| 指标 | 目标 |
|---|---|
| hook 内耗时 | < 50 µs（快照拷贝 + try_send + 一次 Mutex clone） |
| 决策线程耗时 | 100~800 ms（可接受，玩家在看训练动画） |
| 通道 | 容量 1，满则丢（`dropped` 计数），下回合重提交 |
| 预取 | turn 变化时立刻提交该回合快照，不等 hook 来要 |

## 可观测

`GET /matsuri/async/status`（待注册）：

```json
{"ok":true,"turn":42,"elapsed_ms":238,"chosen":0,
 "source":"matsuri","computed":41,"missed":1,"rejected":0,"dropped":0}
```

`missed` 持续上升 = 决策线程跟不上，要么降 rollout 数，要么提前预取；
`dropped` 上升 = 提交太频繁（同一回合重复 submit）。

## 接入（3 行）

```rust
mod decisor_async;
static DECIDER: OnceLock<AsyncDecider> = OnceLock::new();
// 启动时（hachimi_init 之后）：
DECIDER.get_or_init(|| AsyncDecider::start(MatsuriScorer::new(cfg)));
```

hook 里：

```rust
let turn = current_turn();
DECIDER.get().map(|d| d.submit(snapshot_from_game()));
if let Some(d) = DECIDER.get().and_then(|d| d.take_for(turn)) {
    return encode_weights(&d.scores);
}
orig(this, cmd_id)          // 回退
```

## 明确不做

- **X25519 / 握手 / 发包链**：不做。决策改为本地替换后不需要跟服务器协商任何密钥。
- **UI 移动（布局/位置 hook）**：以后再说。
- **3 帧加速（targetFrameRate）**：一直没成功，以后再说。
