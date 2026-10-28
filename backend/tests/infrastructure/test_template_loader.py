"""模板 TOML 加载：解析 + validate，坏定义在启动/建库时炸，不进库。"""

import pytest

from pmstudio.domain.shared.errors import BadTemplateDefinition
from pmstudio.domain.shared.ids import SectionId
from pmstudio.domain.shared.template import TemplateRole, standard_template


def test_templates_dir_sits_next_to_the_loader():
    """资源路径守卫（M5 教训）：目录常量住在读它的模块旁边，调用方 import，不许拼。"""

    from pathlib import Path

    from pmstudio.infrastructure.templates import loader

    assert loader.TEMPLATES_DIR == Path(loader.__file__).parent
    assert (loader.TEMPLATES_DIR / "standard.toml").is_file()
    assert (loader.TEMPLATES_DIR / "light.toml").is_file()


def test_standard_toml_matches_code_built_standard():
    """双源钉死：手写 TOML 与 `standard_template()` 不一致就红，改一边必须改另一边。"""

    from pmstudio.infrastructure.templates import loader

    assert loader.load_template_file(loader.TEMPLATES_DIR / "standard.toml") == (
        standard_template()
    )


def test_light_toml_has_six_sections_and_three_anchors():
    from pmstudio.infrastructure.templates import loader

    light = loader.load_template_file(loader.TEMPLATES_DIR / "light.toml")
    light.validate()

    assert light.template_id == "light"
    assert len(light.sections) == 6
    assert light.anchor(TemplateRole.FEATURE) == SectionId("s5")
    assert light.anchor(TemplateRole.OPEN_QUESTIONS) == SectionId("s11")
    assert light.anchor(TemplateRole.VERSION_HISTORY) == SectionId("s13")


def test_template_missing_anchor_is_rejected(tmp_path):
    from pmstudio.infrastructure.templates import loader

    bad = tmp_path / "bad.toml"
    bad.write_text(
        'template_id = "bad"\ntitle = "坏"\n'
        '[[sections]]\nid = "s5"\ntitle = "功能"\npurpose = "x"\nrole = "feature"\n',
        encoding="utf-8",
    )

    with pytest.raises(BadTemplateDefinition):
        loader.load_template_file(bad)


def test_template_unknown_role_and_slot_kind_are_rejected(tmp_path):
    from pmstudio.infrastructure.templates import loader

    bad_role = tmp_path / "bad_role.toml"
    bad_role.write_text(
        'template_id = "r"\ntitle = "x"\n'
        '[[sections]]\nid = "s1"\ntitle = "t"\npurpose = "p"\nrole = "nope"\n',
        encoding="utf-8",
    )
    with pytest.raises(BadTemplateDefinition):
        loader.load_template_file(bad_role)

    bad_kind = tmp_path / "bad_kind.toml"
    bad_kind.write_text(
        'template_id = "k"\ntitle = "x"\n'
        '[[sections]]\nid = "s8"\ntitle = "t"\npurpose = "p"\nrole = "plain"\n'
        '[[sections.slots]]\nname = "performance"\ntitle = "性能"\nkind = "nope"\n',
        encoding="utf-8",
    )
    with pytest.raises(BadTemplateDefinition):
        loader.load_template_file(bad_kind)


def test_loading_a_directory_rejects_duplicate_template_ids(tmp_path):
    from pmstudio.infrastructure.templates import loader

    for name in ("a.toml", "b.toml"):
        (tmp_path / name).write_text(
            'template_id = "same"\ntitle = "x"\n'
            '[[sections]]\nid = "s5"\ntitle = "t"\npurpose = "p"\nrole = "feature"\n'
            '[[sections]]\nid = "s11"\ntitle = "t"\npurpose = "p"\nrole = "open_questions"\n'
            '[[sections]]\nid = "s13"\ntitle = "t"\npurpose = "p"\nrole = "version_history"\n',
            encoding="utf-8",
        )

    with pytest.raises(BadTemplateDefinition):
        loader.load_template_dir(tmp_path)


def test_seed_builtin_templates_is_idempotent_and_keeps_customs():
    """启动 seed：内置 upsert 反复种不炸，自定义模板碰不到。"""

    from pmstudio.domain.shared.ids import SectionId
    from pmstudio.domain.shared.template import DocumentTemplate, TemplatedSection, TemplateRole
    from pmstudio.infrastructure.postgres.repositories import SqliteTemplateRepository
    from pmstudio.infrastructure.templates import loader
    from tests.support.postgres import open_direct

    connection = open_direct()
    repo = SqliteTemplateRepository(connection)
    custom = DocumentTemplate(
        template_id="tpl-x",
        title="自建",
        sections=(
            TemplatedSection(
                section_id=SectionId("s5"), title="t", purpose="p", role=TemplateRole.FEATURE
            ),
            TemplatedSection(
                section_id=SectionId("s11"),
                title="t",
                purpose="p",
                role=TemplateRole.OPEN_QUESTIONS,
            ),
            TemplatedSection(
                section_id=SectionId("s13"),
                title="t",
                purpose="p",
                role=TemplateRole.VERSION_HISTORY,
            ),
        ),
    )
    repo.save(custom, builtin=False)

    first = loader.seed_builtin_templates(repo)
    second = loader.seed_builtin_templates(repo)

    assert first == second
    assert "standard" in first and "light" in first
    # `get()` 改报 `TemplateEntry`（带 builtin 标记，`02` §11.0.70）：需求变更
    assert repo.get("tpl-x").template == custom  # 自定义的没被 seed 碰
    connection.close()
