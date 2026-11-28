# CI 最近一次失败日志

- run: 46110360564
- sha: 6cdc2fa4cea4031e40bb7c0c80a81c3eb4501b7b

## 大小
```
```
## cargo error
```
```
## cargo 尾部
```
```
## patch 尾部
```
  /api/ai/tables: 2 line(s)
  /api/ai/weightcalls: 2 line(s)
patched src/lib.rs: 1034738 -> 1038049 bytes
  WB_OVR_PATHS: 3 line(s)
  Android/data/jp.co.cygames.umamusume/files/uma_so: 1 line(s)
  fn wb_override_lookup: 1 line(s)
  wb_override_lookup(arg0): 1 line(s)
  /api/ai/override: 2 line(s)
patched src/lib.rs: 1038049 -> 1040892 bytes
  fn find_method_entry: 1 line(s)
  find_method_entry(class, "GetBaseWeight": 2 line(s)
  fn method_entry_probe_json: 1 line(s)
  /api/ai/entry: 2 line(s)
patched src/lib.rs: 1040892 -> 1046117 bytes
  fn mt_recompute: 1 line(s)
  fn mt_weight_for_arg: 1 line(s)
  fn mt_json: 1 line(s)
  mt_weight_for_arg(arg0): 1 line(s)
  /api/ai/matsuri: 2 line(s)
patched src/lib.rs: 1046117 -> 1049495 bytes
  struct MtIdentity: 1 line(s)
  fn mt_read_identity: 1 line(s)
  fn mt_deck_json: 1 line(s)
  mt_read_identity(): 3 line(s)
  /api/ai/deck: 2 line(s)
patched src/lib.rs: 1049495 -> 1053298 bytes
  fn mt_deck_raw_json: 1 line(s)
  as_value_inline128: 1 line(s)
  as_ref_ptr: 1 line(s)
  /api/ai/deckraw: 2 line(s)
patched src/lib.rs: 1053298 -> 1053429 bytes
  read_obscured_int_at(e, 16): 6 line(s)
  read_obscured_int_at(e, 36): 7 line(s)
  read_obscured_int_at(e, 56): 4 line(s)
  seq_item(arr, i): 2 line(s)
patched src/lib.rs: 1053429 -> 1054501 bytes
  fn mt_start_worker: 1 line(s)
  matsuri-worker: 2 line(s)
  mt_start_worker();: 1 line(s)
  mt_recompute() 直接调用点: 0（应为 1，仅 worker 内）
patched src/lib.rs: 1054501 -> 1055879 bytes
  dataset_missing: 1 line(s)
  "acts":[]: 1 line(s)
  notes: 2 line(s)
  ::warning:: 仍有 dataset_null 字样（应为 0，旧分支已替换）
patched src/lib.rs: 1055879 -> 1057226 bytes
  MT_LAST_HIT: 3 line(s)
  idle: 14 line(s)
  chara_class_mismatch: 3 line(s)
::error::ci_patch12: P2 sigsetjmp call sites=2（应 >= 10） | file=src/lib.rs anchor_head='// ★ v3.22.35: If sigsetjmp was set (push thread), longjmp back instead of killing process' hit_lines=[1611]
```
