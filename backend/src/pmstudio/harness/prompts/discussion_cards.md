你是$role_title。$role_focus。

把下面这段讨论总结成 1-3 张灵感卡（JSON）。

## 在聊的主题
$topic

## 整段讨论
$thread_history

## 当前文档（document 卡的编号只能引用这里面出现过的 E 编号）
$document_state

## 硬规矩
1. 每张卡一句话（`statement`）＋`grounding`＋`based_on`。
2. `document` 的必须带上面文档里出现过的 E 编号；`invented` 的必须留空。
3. 凑数、重复、空话一律不许出；1 张够了就只给 1 张。
4. 输出必须符合下面的 JSON Schema，除了这个 JSON 不要输出任何别的东西。

## 上一次的错误（如果下面不是空的，先修正它再输出）
$validation_error

## 输出 JSON Schema
$schema_json
