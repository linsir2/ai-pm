"""固定模拟使用者（persona）目录：五个只由**使用情境与行为**定义的走查者（M11）。

与席位对照：席位是"领域专家"（判断文档作为专业产物缺什么），persona 是"使用行为模拟器"
（照文档走一遍会发生什么）。两者是不同物种——所以独立文件、独立命名空间（`persona.*`）、
不进 `seats/definitions/`（那个目录的 `always_on/sections/fields` 字段对 persona 全无意义，
塞进去会立刻破 `test_limits` 的"10 个席位"契约）。
"""
