"""不变量码。每个码必须至少有一个正例与一个反例测试（见 test_invariant_matrix.py）。"""

from enum import StrEnum


class DInvariant(StrEnum):
    D1 = "D1"  # 十三章固定，不可增删重排
    D2 = "D2"  # block id 永久稳定；删除留墓碑，编号不复用
    D3 = "D3"  # 只有 s5 能长 feature 子树，且需用户授权
    D4 = "D4"  # 同一父节点下字段名唯一
    D5 = "D5"  # 被手改过的 block 不能被静默覆盖
    D6 = "D6"  # 一次写入=一版；版本只增不改；回退产生新版本


class EInvariant(StrEnum):
    E1 = "E1"  # 编号全局唯一、只增、永不复用
    E2 = "E2"  # 修订只能新增 + supersedes，不能改写历史
    E3 = "E3"  # agent_derived 必带 derived_from 且指向已存在编号
    E4 = "E4"  # 可追溯：链必须落到用户输入或问卷答案（服务层校验）


class RInvariant(StrEnum):
    R1 = "R1"  # 一轮绑定一次用户输入
    R2 = "R2"  # step 只能按序推进；waiting_on 非空不能推进
    R3 = "R3"  # 席位轮最多 3 次
    R4 = "R4"  # 一批问卷最多 5 题
    R5 = "R5"  # 草案只能由整合产生，每条提案必须带 refs
    R6 = "R6"  # 答案必须带 question_id，且只能答本轮的问题
    R7 = "R7"  # 席位轮只接受开场卡编制内的席位


class LabelInvariant(StrEnum):
    LB1 = "LB1"  # 偏好不进正文（进偏好区）
    LB2 = "LB2"  # 猜测必须升格为假设（带失败后果与验证方式）
    LB3 = "LB3"  # 决定必须记决策者


class AgentInvariant(StrEnum):
    AG1 = "AG1"  # 空意见不等于没意见：必须给依据


class GlobalInvariant(StrEnum):
    GB1 = "GB1"  # 先有编号再有内容


class XInvariant(StrEnum):
    X1 = "X1"  # 假设必须可证伪：写清怎么被推翻、需要什么真实证据
    X2 = "X2"  # 一趟调研的产物有上限（假设 6 / 盲区 3）
    X3 = "X3"  # 候选只能被过目一次：new → kept|dismissed，单向、幂等；没跑完不许过目
    X4 = "X4"  # 探索运行状态转换合法：只有 FAILED 能 resume；终态不可改


ALL_INVARIANTS: tuple[StrEnum, ...] = (
    *DInvariant,
    *EInvariant,
    *RInvariant,
    *LabelInvariant,
    *AgentInvariant,
    *GlobalInvariant,
    *XInvariant,
)
