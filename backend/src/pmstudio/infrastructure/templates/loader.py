"""模板 TOML 加载：解析成 `DocumentTemplate` + `validate`，坏定义直接炸。

目录常量住在这里（M5 教训：调用方 import `TEMPLATES_DIR`，不许自己拼路径）。
`tomllib` 是标准库——domain 纯洁守卫只拦外部包，且解析本就住 infrastructure。
"""

import tomllib
from pathlib import Path

from pmstudio.domain.shared.errors import BadTemplateDefinition
from pmstudio.domain.shared.template import DocumentTemplate, template_from_dict

TEMPLATES_DIR = Path(__file__).parent


def seed_builtin_templates(repository, *, directory: Path = TEMPLATES_DIR) -> tuple[str, ...]:
    """内置模板 seed（幂等 upsert）：TOML 是定义源，表是运行时源。

    改 TOML 发版即改内置（upsert 覆盖）；自定义模板 id 不同，碰不到。
    `repository` 按鸭子类型调 `.save(template, builtin=True)`——真库与替身通用。
    返回种下的 id（启动日志/测试断言用）。
    """

    seeded: list[str] = []
    for template in load_template_dir(directory).values():
        repository.save(template, builtin=True)
        seeded.append(template.template_id)
    return tuple(seeded)


def load_template_file(path: Path) -> DocumentTemplate:
    """读一份 TOML → `DocumentTemplate`（含 `validate`，坏的进不来）。"""

    with open(path, "rb") as stream:
        data = tomllib.load(stream)
    return template_from_dict(data)


def load_template_dir(directory: Path) -> dict[str, DocumentTemplate]:
    """读一整个目录：template_id 重复直接炸（两个文件宣称同一个模板就是事故）。"""

    loaded: dict[str, DocumentTemplate] = {}
    for path in sorted(directory.glob("*.toml")):
        template = load_template_file(path)
        if template.template_id in loaded:
            raise BadTemplateDefinition(
                "模板 id 重复", template_id=template.template_id, path=str(path)
            )
        loaded[template.template_id] = template
    return loaded
