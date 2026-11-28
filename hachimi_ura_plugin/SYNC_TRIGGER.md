# 同步触发（2026-11-29）

`sync-upstream` 已把 `delightedMaster/uma-so` 的 `hachimi_ura_plugin/` 全量镜像到本仓
（commit `94eff007`，含 `ci_patch.py` … `ci_patch19.py`、`patch_gamedata.py`、`extra_gamedata.json`）。

GITHUB_TOKEN 推送的提交不会触发 workflow，所以用本文件手动点一次 `build-so`，
验证「上游 12–19 全链 + patch_gamedata + distilled.toml」在本仓也能绿。

下次 `sync-upstream` 运行时本文件会被清掉，属预期。