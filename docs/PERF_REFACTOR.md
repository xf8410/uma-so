# 性能与稳定性重构方案（2.33.0 / 3.29.8 实测）

## 结论先说：不换语言

保留 Rust。理由不是"喜欢 Rust"，是三条硬指标：

1. **崩溃面**：24k 行里已有 24 个 `Mutex`、若干 `static mut`。换成 C/C++，同一份逻辑的 UB 面只会更大
   （v3.28.11 的 `usize`↔`u128` 就是 ABI 事故，C 里连编译器都不会提醒）。
2. **已有资产**：hook 基础设施（Hachimi Interceptor API）、il2cpp 解析、MDB 读取全在 Rust 侧，
   换语言等于重写 2.4 万行并重踩一遍 VERSION_BUGS.md。
3. **真正的瓶颈不在语言**：在**热路径上做了分配、加锁、序列化**。这与语言无关，改架构即可。

要"兼容 + 不卡"，改的是下面五处，不是换 Rust。

---

## 一、热路径清点（都在游戏网络线程上）

| 位置 | 现状 | 问题 |
|---|---|---|
| `MakeMd5` hook | `MD5_LOG: Mutex<Vec<(String,String)>>`，每次 push 两个 String | 每个请求两次堆分配 + 全局锁 + **无上限增长** |
| `UnityWebRequest` 观察 | `UNITY_OBSERVATIONS: Mutex<Vec<UnityRequestObservation>>`，含 `body_hex: String` | 每次 clone 整份 hex（几十 KB），锁内做 |
| `Compress→Post` 配对 | `static mut PENDING_REQ_BODY: Option<Vec<u8>>` | **裸静态跨线程读写 = 数据竞争**，是偶发卡死/崩溃的头号嫌疑 |
| `ureq::get` (21694/21731) | 同步、无显式 timeout | 自更新检查在网络环境下可能吊住 |
| HTTP 服务 (7064) | `TcpListener` + **每请求 spawn 线程**，无上限 | 并发扫描/大 dump 会瞬间几十线程 |

## 二、重构五项（按 ROI 排序）

### R1 干掉 `static mut`（P0，先做）
```rust
// 现在
static mut PENDING_REQ_BODY: Option<Vec<u8>> = None;
// 改成
static PENDING_REQ_BODY: Mutex<Option<Vec<u8>>> = Mutex::new(None);
```
同理 `MAKEMD5_ADDR / COMPUTEHASH_ADDR / POST_ADDR`：启动期用 `OnceLock<usize>`，避免运行期裸读写。

### R2 热路径零分配（P0）
- `MD5_LOG` 改**定长环形缓冲**：`[u64; 256]` 存 input/output 的前 8 字节 hash，不做 String。
- 观察记录存 `Arc<[u8]>` 引用，不 clone hex；hex 只在端点被请求时才生成。
- 采样开关：默认 1/8 采样（`/api/sniff/toggle` 可调），抓包调试时全采。

### R3 线程池（P1）
`thread::spawn` per request → 固定 4~8 线程 + 有界队列（满了直接 503），
防止 `/il2cpp/dump` 这类重端点在弱网/并发下把线程数打爆。

### R4 所有出站请求加硬超时（P0）
```rust
ureq::AgentBuilder::new()
    .timeout_connect(Duration::from_millis(300))
    .timeout_read(Duration::from_millis(800))
    .build()
```
并且**禁止在 hook 回调里发起任何出站请求**（v3.24.52 开机 ANR 的根因就是它）。

### R5 日志/落盘批量化（P2）
`ura_log` 先进内存环形缓冲（8KB），后台每 500ms 批量落盘；崩溃时用 `atexit` 刷一次。

## 三、"偶尔卡在连接中"的取证清单

先分清是**动画在转**还是**画面冻住**：

| # | 假设 | 验证 |
|---|---|---|
| 1 | 我们的 hook 漏转发回调 | 按 request id 记录「收到→转发→游戏回执」，看卡住那次缺哪步 |
| 2 | 初始化竞态（首连时 hook 未装好） | 记录 `install_hooks` 与首个请求的单调时钟顺序，冷启 20 次对照 |
| 3 | 同步等待死锁（主线程 join，工作线程等主线程） | 卡住瞬间抓全线程栈，看两端是否互等 |
| 4 | 迟到回调覆盖当前状态 | 每次连接尝试递增 id，检查旧 id 是否写入当前状态 |
| 5 | 回调线程错误（工作线程直接调 Unity 主线程逻辑） | 记录回调线程 id |
| 6 | **我们侧的锁竞争**（本文 R1/R2） | `/debug/ringlog` 看 MakeMd5/UnitySend hook 的耗时 p99 |

我们自己的三个具体嫌疑点已定位：`PENDING_REQ_BODY` 的 `static mut`、`MD5_LOG` 无上限、
`UNITY_OBSERVATIONS` 锁内 clone hex。这三处都在网络线程上。

## 四、验收指标

| 指标 | 目标 |
|---|---|
| hook 内耗时 p99 | < 200 µs（MakeMd5 / UnitySend / Compress / Post） |
| `/health` 响应 | < 5 ms |
| 并发 20 请求 | 线程数 ≤ 8，无 5xx |
| 连续 50 次冷启动登录 | 0 次卡"连接中" |
| 单局育成（78 回合） | 无崩溃、无掉帧尖峰 > 100 ms |

## 五、语言对比（留档，供以后判断）

| 方案 | 优点 | 代价 |
|---|---|---|
| **Rust（保留）** | 内存安全、已有基础设施、panic 可 catch | 编译慢、target 体积大 |
| C/C++ | 编译快、体积小 | UB 面大、无 panic 隔离、要重写 2.4 万行 |
| C# + IL2CPP 模式 | 与游戏同构 | hook 层仍需 native，收益无 |
| Zig | 无 GC、C ABI 友好 | 生态/hook 库缺，重写成本最高 |

结论：**语言不动，动热路径**。
