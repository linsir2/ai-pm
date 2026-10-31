import json
import re

from pmstudio.application import context
from pmstudio.application.agents.schemas import ProposalDraft
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp
from pmstudio.harness.prompt import PROMPTS_DIR, FilePromptLibrary
from tests.application.test_exact_write_consumers import mechanical_round
from tests.domain.test_exact_write_guards import apply, instruction
from tests.support.builders import PROJECT


async def integration_request():
    harness, rid = await mechanical_round(target=Target.body("s1"), text="用户已经修订规则")
    document = harness.documents.get(PROJECT)
    flow = "1. 原首步；2. 保留第二步；3. 保留第三步；4. 保留第四步"
    apply(document, instruction("flow#01", flow, base=2,
          target=Target.body("s5", feature_slug="order-export", field="main_flow")))
    apply(document, instruction("s8.cost", "撤槽", op=WriteOp.REMOVE, base=3,
          target=Target.body("s8", field="cost")))
    apply(document, instruction("cost#01", "每月成本一百元", base=4,
          target=Target.body("s8", field="cost")))
    apply(document, instruction("pref#01", "偏好短句", base=5, target=Target.preference()))
    harness.documents.save(document)
    use_case = harness.integrate_round()
    use_case._integrator._prompts = FilePromptLibrary(PROMPTS_DIR)
    await use_case(round_id=rid)
    request = next(r for r in harness.gateway.requests if r.purpose == "integration")
    return document, request, "\n".join(m.content for m in request.messages), flow


async def test_actual_model_request_has_one_full_document_plus_real_id_target_index():
    document, request, text, flow = await integration_request()
    index = context.render_existing_entries(document)
    assert "flow#01 → s5/order-export.main_flow" in index
    assert "cost#01 → s8.cost" in index
    assert "pref#01 → preference" in index
    assert "~2" not in index
    assert flow not in index
    assert "每月成本一百元" not in index
    assert text.count(flow) == 1
    assert "feature#order-export" in text and "main_flow：" in text
    assert "条目 flow#01" in text and "（E1）" in text
    assert request.max_tokens == 8457
    assert "一次整合最多 21 条" in text


async def test_consumed_prompt_teaches_complete_replace_and_explicit_revision():
    _, _, text, _ = await integration_request()
    assert "只提文档与历史答案里还没有的内容" not in text
    assert "未改句" in text and "支持" in text and "refs" in text
    assert "用户已修订" in text and "不是待选冲突" in text
    example = json.loads(re.search(r"```json\n(.*?)\n```", text, re.S).group(1))
    parsed = ProposalDraft.model_validate(example)
    assert parsed.op == "replace"
    assert parsed.entry_id == "flow-demo#01"
    assert parsed.evidence_refs == ["E1", "E2"]
    assert parsed.text == "1. 先校验权限；2. 选择订单；3. 生成文件；4. 下载文件"
    assert parsed.target.field == "main_flow"
