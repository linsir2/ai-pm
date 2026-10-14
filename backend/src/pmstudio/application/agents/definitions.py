"""8 个 agent 的声明实例。

每个 agent 的身份信息（purpose / kind / prompt / packet / slots）统一管理在这里。
agent 类本身通过 `definition = ANALYZER` 引用对应的声明。
"""

from pmstudio.application.agents.schemas import (
    CardsPacket,
    ClaimsPacket,
    DiscussionReply,
    InquiryPacket,
    IntegrationPacket,
    MemoryPacket,
    SeatPacket,
    SimInquiryPacket,
    SimReviewPacket,
)
from pmstudio.harness.agents.agent import AgentDefinition

ANALYZER = AgentDefinition(
    agent_id="analyzer",
    purpose="claims",
    kind="claims",
    prompt="analyzer",
    packet=ClaimsPacket,
    slots=(
        "document_state",
        "user_input",
        "labels",
        "sections",
        "feature_fields",
        "section_slots",
        "seat_catalog",
        "external_material",
        "project_context",
        "schema_json",
        "validation_error",
    ),
)

SEAT_REVIEWER = AgentDefinition(
    agent_id="seat_reviewer",
    purpose="seat_review",
    kind="seat_review",
    prompt="seat_review",
    packet=SeatPacket,
    slots=(
        "seat_title",
        "seat_focus",
        "document_state",
        "claims",
        "opinions_so_far",
        "answered",
        "project_memory",
        "project_context",
        "observations_index",
        "external_material",
        "allowed_eids",
        "schema_json",
        "validation_error",
    ),
)

INTEGRATOR = AgentDefinition(
    agent_id="integrator",
    purpose="integration",
    kind="integration",
    prompt="integrator",
    packet=IntegrationPacket,
    slots=(
        "document_state",
        "claims",
        "opinions",
        "answered",
        "asked_questions",
        "skipped",
        "project_memory",
        "project_context",
        "observations_index",
        "digests",
        "inspirations_index",
        "entry_ids",
        "external_material",
        "limits",
        "feature_fields",
        "section_slots",
        "authorized_features",
        "open_items",
        "previous_list_items",
        "contradictions",
        "seat_conflicts",
        "schema_json",
        "validation_error",
    ),
)

INQUIRER = AgentDefinition(
    agent_id="inquirer",
    purpose="inquiry",
    kind="inquiry",
    prompt="inquiry",
    packet=InquiryPacket,
    slots=(
        "scope",
        "document_state",
        "project_memory",
        "schema_json",
        "validation_error",
    ),
)

DISCUSSER_SAY = AgentDefinition(
    agent_id="discusser_say",
    purpose="discussion",
    kind="discussion",
    prompt="discussion_say",
    packet=DiscussionReply,
    slots=(
        "role_title",
        "role_focus",
        "topic",
        "question",
        "related_findings",
        "thread_history",
        "document_state",
        "schema_json",
        "validation_error",
    ),
)

DISCUSSER_CARDS = AgentDefinition(
    agent_id="discusser_cards",
    purpose="discussion_cards",
    kind="discussion_cards",
    prompt="discussion_cards",
    packet=CardsPacket,
    slots=(
        "role_title",
        "role_focus",
        "topic",
        "thread_history",
        "document_state",
        "schema_json",
        "validation_error",
    ),
)

MEMORY_CURATOR = AgentDefinition(
    agent_id="memory_curator",
    purpose="memory_curation",
    kind="memory_curation",
    prompt="memory_curator",
    packet=MemoryPacket,
    slots=("memory_state", "round_digest", "schema_json", "validation_error"),
)

SIM_INQUIRY = AgentDefinition(
    agent_id="sim_inquiry",
    purpose="sim_inquiry",
    kind="sim_inquiry",
    prompt="sim_inquiry",
    packet=SimInquiryPacket,
    slots=(
        "document_state",
        "claims",
        "external_material",
        "project_memory",
        "schema_json",
        "validation_error",
    ),
)

SIM_REVIEW = AgentDefinition(
    agent_id="sim_review",
    purpose="sim_review",
    kind="sim_review",
    prompt="sim_review",
    packet=SimReviewPacket,
    slots=(
        "document_state",
        "claims",
        "external_material",
        "project_memory",
        "persona",
        "targets",
        "schema_json",
        "validation_error",
    ),
)

ALL_AGENTS = (
    ANALYZER,
    SEAT_REVIEWER,
    INTEGRATOR,
    INQUIRER,
    DISCUSSER_SAY,
    DISCUSSER_CARDS,
    MEMORY_CURATOR,
    SIM_INQUIRY,
    SIM_REVIEW,
)
