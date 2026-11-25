# 3.29.x 端点差集与补齐范围

## 核对结果（3.29.8 运行时 164 条 vs 3.29.1 源码 ENDPOINTS）

初筛 11 条，逐条 grep 后确认源码确实没有：

| 端点 | 职责 | 是否补 |
|---|---|---|
| `/api/prefix` | 资源下载前缀解析 | ✅ |
| `/api/prefix/diag` | 前缀解析诊断 | ✅ |
| `/api/prefix/reset` | 前缀缓存重置 | ✅ |
| `/api/decrypt` | 本地资源/包体解密（按 key 参数） | ✅ |
| `/api/request` | 受控单次出站请求（只回显，不改状态） | ✅ |
| `/api/http_probe` | 出站连通性探针 | ✅ |
| `/debug/selftest` | 全链路自检（il2cpp/mdb/http/hook） | ✅ |
| `/debug/ringlog` | 环形日志（内存内最近 N 条） | ✅ |
| `/debug/summary_integrity` | summary 一致性校验 | ✅ |
| `/api/x25519` | 握手公钥交换 | ❌ 不做 |
| `/api/x25519/install` | 握手密钥安装 | ❌ 不做 |

`/`、`/config.html` 源码已有（初筛误报，正则漏匹配数组项）。

## 为什么砍掉握手

决策改成游戏内本地替换后，不再需要与服务器协商密钥、也不再需要构造加密请求。
`/api/decrypt`、`/api/request` 保留是因为它们用于**离线分析抓包**（本地单次、只回显），
与握手链无关。

## 补齐顺序

1. `/debug/selftest` → 先有自检，后面每一步都能验
2. `/debug/ringlog` → 环形日志，给异步决策器与 hook 用
3. `/debug/summary_integrity` → summary 一致性（v3.28.15 崩溃就是 summary 读脏对象）
4. `/api/prefix*` 三条 → 资源前缀（现有 resource_* 端点同一数据源）
5. `/api/decrypt`、`/api/request`、`/api/http_probe` → 离线分析用，最后补

## 边界

- 只读优先；`/api/request` 必须显式参数 + 单次 + 不回写游戏状态。
- 全部端点进 `ENDPOINTS` 常量表，`/health` 与 404 的 available 同源。
- 不引入 UI 移动与帧率相关 hook。
