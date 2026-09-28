"""出处编号的写法归一化：认写法差异，不认幻觉。

真跑实测（报告 F3）：席位把 `E8` 写成 `E08`、把上一轮的提案编号
`r-f2a00f4e#08` 当出处编号——前一种是同一个编号的另一种写法（该认），
后一种是另一个命名空间（该判死，只是提示要说清楚）。
"""

from pmstudio.application.evidence_refs import candidates_for, normalize_ref
from pmstudio.domain.shared.ids import EvidenceId

KNOWN = frozenset(
    {EvidenceId("E1"), EvidenceId("E8"), EvidenceId("E27"), EvidenceId("E100")}
)


def test_leading_zeros_fall_back_to_the_real_id():
    assert normalize_ref("E08", known=KNOWN) == "E8"
    assert normalize_ref("E000100", known=KNOWN) == "E100"


def test_case_and_spaces_are_ignored():
    assert normalize_ref(" e8 ", known=KNOWN) == "E8"
    assert normalize_ref("Ｅ８", known=KNOWN) == "E8"  # 全角也认


def test_id_that_does_not_exist_stays_untouched():
    # 不认识就是不认识：交给失败出口，不给"编造编号"开后门
    assert normalize_ref("E999", known=KNOWN) == "E999"


def test_proposal_ids_are_not_evidence_ids():
    assert normalize_ref("r-f2a00f4e#08", known=KNOWN) == "r-f2a00f4e#08"


def test_candidates_point_at_neighbours():
    assert "E27" in candidates_for("E28", known=KNOWN)


def test_candidates_are_empty_when_there_is_no_number():
    assert candidates_for("seat.data_scale:O1", known=KNOWN) == ()
