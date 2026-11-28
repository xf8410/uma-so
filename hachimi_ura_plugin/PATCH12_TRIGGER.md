# ci_patch12 构建触发

本文件只用于触发 `build-so`（workflow 的 paths 只覆盖 `hachimi_ura_plugin/**`）。
`ci_patch12.py` 改动与 CI 日志增强已分别提交：

- `6deaafe1` ci_patch12：P6 安装链补上 `install_selector_hooks`，加锚点诊断
- `d6cbe3ac` ci：patch 阶段写 `/tmp/patch.log`，失败时落 `docs/CI_LAST_ERROR.md`

构建结果看 Actions；失败时错误摘要已自动写回 `docs/CI_LAST_ERROR.md` 的「patch 尾部」小节。