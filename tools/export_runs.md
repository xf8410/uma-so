# 对局数据导出契约（v1）

## 触发
育成结束（`WorkSingleModeData` 终局 / 比赛结算后）或用户手动 `/export/run`。

## 端点
```
POST http://127.0.0.1:18765/export/run
body: {"mode":"auto"|"manual"}
resp: {"ok":true,"run_id":"20261125T142530Z-8f3a","bytes":123456,"sha256":"..."}
```
落盘：`/sdcard/Download/uma_runs/<run_id>.json`（先写文件，再上传）

## 结构
```json
{
  "schema": 1,
  "run_id": "...",
  "account_hash": "sha256:...",
  "uma": {"id":105902,"name":"...","stars":3},
  "cards": [{"id":303174,"lb":4}, "..."],
  "scenario": {"id":3,"name":"matsuri"},
  "inherit": {"blue":[18,0,0,0,0],"extra":[0,0,90,90,0,10],"level":70},
  "turns": [
    {"turn":0,"vital":100,"motivation":2,"status":[120,95,88,90,102],
     "gains":{"speed":[12,3],"stamina":[0,0],"power":[8,1],"guts":[0,0],"wit":[6,0]},
     "partners":[[101011,1],[101012,2]],
     "hint":0.12,"gauge":0.0,"pt":40,
     "candidates":["speed","stamina","power","guts","wit","rest","outing"],
     "chosen":"speed","source":"matsuri","score":["..."]}
  ],
  "result": {"status":"finished","final_status":[1200,800,900,600,1100],"rank":"S",
             "pt_total":1234,"skills":["..."],"races":[...]}
}
```

## 上传
优先 `POST /api/github/upload`（SO 内已有 token 通道 `files/uma_github_token.txt`），
目标 `xf8410/uma-so-private` 的 `data/runs/<YYYY-MM>/<run_id>.json`；失败落本地队列，下次启动重试。

## 蒸馏口径
`data-distill.yml` 每日把 `data/runs/**` 汇总为 `data/distill/<date>.jsonl`，
一行一回合，字段与 matsuri `record_ordered_rollouts` 对齐（配对分 / 截断前缀 / 整局轨迹三类样本）。
