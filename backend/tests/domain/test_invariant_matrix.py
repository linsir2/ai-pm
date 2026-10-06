"""退出条件的机械化版本：每个不变量码都必须有正例与反例测试。"""

from pmstudio.domain.shared.invariants import ALL_INVARIANTS


def test_every_invariant_has_positive_and_negative_cases(request):
    names = [item.name for item in request.session.items]
    missing: list[str] = []
    for invariant in ALL_INVARIANTS:
        code = invariant.value
        if not any(name.startswith(f"test_{code}_ok_") for name in names):
            missing.append(f"{code} 缺正例：应有一个 test_{code}_ok_* ")
        if not any(name.startswith(f"test_{code}_bad_") for name in names):
            missing.append(f"{code} 缺反例：应有一个 test_{code}_bad_* ")
    assert missing == []


def test_matrix_covers_every_invariant_code():
    codes = {invariant.value for invariant in ALL_INVARIANTS}
    assert codes == {
        "D1",
        "D2",
        "D3",
        "D4",
        "D5",
        "D6",
        "E1",
        "E2",
        "E3",
        "E4",
        "R1",
        "R2",
        "R3",
        "R4",
        "R5",
        "R6",
        "R7",
        "R8",
        "LB1",
        "LB2",
        "LB3",
        "AG1",
        "GB1",
        "X1",
        "X2",
        "X3",
        "X4",
        "T1",
        "T2",
        "T3",
        "T4",
    }
