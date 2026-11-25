"""Who may see what of a parent's data (parent-first sharing, brief §6.5).

One copy of the rules, used by the family dashboard API and by the family's read-only MCP
tools alike:
- the parent always sees their own data;
- nothing is shared before the parent agrees or after consent is revoked; while paused,
  what was already shared stays visible (nothing new is logged while paused);
- otherwise a family member sees a category (readings / alerts / medicines) only if the
  parent shares it; red alerts always reach the family.
"""

from app.storage.base import Store
from app.storage.models import Alert, Member, ShareCategory


def consented(viewer: Member, parent: Member) -> bool:
    """Nothing is shared before consent, or after it is withdrawn (the parent sees all)."""
    return viewer.id == parent.id or parent.consent_status in ("granted", "paused")


def can_see(store: Store, viewer: Member, parent: Member, category: ShareCategory) -> bool:
    if viewer.id == parent.id:
        return True  # the parent always sees their own data
    if viewer.family_id != parent.family_id or not consented(viewer, parent):
        return False
    return store.get_sharing(parent.id).allows(category)


def alert_visible(store: Store, viewer: Member, parent: Member, alert: Alert) -> bool:
    """Red alerts always reach the family; amber only if the parent shares alerts."""
    if alert.level == "red":
        return viewer.family_id == parent.family_id and consented(viewer, parent)
    return can_see(store, viewer, parent, "alerts")
