# CI 最近一次失败日志

- run: 46110706229
- sha: 9eb276bb2fba1f5ccaef3b0d61642abcb1137a51

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
ci_patch12_pre: patched ci_patch12.py (regex/threshold)
  P2 sigsetjmp call sites: 10
patched src/lib.rs: 1057226 -> 1064177 bytes
patched src/matsuri_bridge.rs: 10987 -> 12007 bytes
  lib · fn read_obscured_int_array: 2
  lib · read_obscured_int_array(chara_effect_ids_arr): 1
  lib · SIGSEGV_TID: 12
  lib · [u8; 320]: 1
  lib · fn mt_thread_attach: 1
  lib · mt_thread_attach();: 1
  lib · fn push_read_enabled: 1
  lib · push_read_enabled(): 2
  lib · install_weight_hook();: 1
  lib · has_act_candidates: 3
  lib · MT_GEN: 4
  lib · find_method_entry(class, "SelectTraining", 1): 1
  bridge · Android/data/jp.co.cygames.umamusume/files/uma_so/matsuri_data: 1
  bridge · static DATA_INIT: Mutex<bool>: 1
  bridge · fn ensure_assets: 1
  bridge · fn ensure_data: 1
  keep · handle_frame: 0
::error::ci_patch12: verify 字面量 handle_frame 丢了
```
