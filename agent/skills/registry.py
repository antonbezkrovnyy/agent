"""Компактный registry доступных skills; исполнение остаётся в create_agent."""

from skills.contracts import SkillDefinition
from skills.entity_pivot import ENTITY_PIVOT
from skills.event_drilldown import EVENT_DRILLDOWN
from skills.incident_dating import INCIDENT_DATING


_SKILLS = {
    INCIDENT_DATING.id: INCIDENT_DATING,
    EVENT_DRILLDOWN.id: EVENT_DRILLDOWN,
    ENTITY_PIVOT.id: ENTITY_PIVOT,
}


def get_skill(skill_id: str | None) -> SkillDefinition | None:
    if not skill_id:
        return None
    return _SKILLS.get(skill_id)


def list_skills() -> list[SkillDefinition]:
    return list(_SKILLS.values())
