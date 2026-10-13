"""Cantonment Board 24-Hour SLA Engine & Automatic Escalation Hierarchy.

Calculates SLA status, remaining time countdowns, timed reminders,
and handles multi-level escalation from Field Staff -> Supervisor -> Manager -> Control Room.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(val: Any) -> Optional[datetime]:
    if not val:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str):
        try:
            # Handle ISO formats
            clean = val.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def calculate_sla_status(
    sla_deadline: Any,
    current_status: str,
    warning_hours: int = 6,
    now: Optional[datetime] = None,
) -> Tuple[str, float, str]:
    """Calculate (sla_status, hours_remaining, human_readable_time_left).

    Statuses:
      - 'within_sla': green
      - 'at_risk': yellow (<= warning_hours left)
      - 'breached': red (deadline passed and not closed/resolved)
    """
    deadline_dt = parse_dt(sla_deadline)
    if not deadline_dt:
        return "within_sla", 24.0, "24h remaining"

    current_time = now or now_utc()

    # Closed or confirmed complaints freeze their SLA outcome
    terminal_statuses = {"CLOSED", "CUSTOMER_CONFIRMED", "RESOLVED"}
    if current_status in terminal_statuses:
        # If was closed within deadline
        diff = (deadline_dt - current_time).total_seconds()
        if diff >= 0:
            return "within_sla", round(diff / 3600.0, 1), "Completed within SLA"
        else:
            return "breached", round(diff / 3600.0, 1), "Resolved past SLA"

    diff_seconds = (deadline_dt - current_time).total_seconds()
    hours_remaining = diff_seconds / 3600.0

    if diff_seconds <= 0:
        overdue_hours = abs(int(hours_remaining))
        overdue_mins = abs(int((diff_seconds % 3600) / 60))
        return "breached", round(hours_remaining, 2), f"Breached by {overdue_hours}h {overdue_mins}m"
    elif hours_remaining <= warning_hours:
        h = int(hours_remaining)
        m = int((diff_seconds % 3600) / 60)
        return "at_risk", round(hours_remaining, 2), f"{h:02d}h {m:02d}m remaining (At Risk)"
    else:
        h = int(hours_remaining)
        m = int((diff_seconds % 3600) / 60)
        return "within_sla", round(hours_remaining, 2), f"{h:02d}h {m:02d}m remaining"


def check_sla_triggers(
    complaint: Dict[str, Any],
    existing_escalations: List[Dict[str, Any]],
    settings: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Determine which SLA reminders or escalations are due right now.

    Configurable reminder triggers:
      - 12h elapsed: 'reminder_12h'
      - 18h elapsed: 'reminder_18h'
      - 22h elapsed: 'final_warning_22h'
      - 24h+ elapsed: 'breach_24h'
    """
    terminal_statuses = {"CLOSED", "CUSTOMER_CONFIRMED", "CANCELLED"}
    if complaint.get("status") in terminal_statuses:
        return []

    received_at = parse_dt(complaint.get("received_at"))
    if not received_at:
        return []

    cfg = settings or {}
    r1_hrs = float(cfg.get("reminder_1_hours", 12))
    r2_hrs = float(cfg.get("reminder_2_hours", 18))
    r3_hrs = float(cfg.get("reminder_3_hours", 22))
    sla_hrs = float(complaint.get("sla_hours") or cfg.get("default_sla_hours", 24))

    current_time = now or now_utc()
    elapsed_hours = (current_time - received_at).total_seconds() / 3600.0

    existing_levels = {e.get("level") for e in existing_escalations}
    new_actions = []

    # 1. 12 Hours: First Reminder
    if elapsed_hours >= r1_hrs and "reminder_12h" not in existing_levels:
        new_actions.append({
            "level": "reminder_12h",
            "notified_to": complaint.get("assigned_staff_name") or "Assigned Field Staff",
            "channel": "whatsapp",
            "action_taken": f"Automatic 12-hour reminder sent to field staff ({int(sla_hrs - elapsed_hours)}h remaining).",
            "escalation_title": "Field Staff SLA Reminder",
            "target_role": "field_staff",
        })

    # 2. 18 Hours: Urgent Reminder
    if elapsed_hours >= r2_hrs and "reminder_18h" not in existing_levels:
        new_actions.append({
            "level": "reminder_18h",
            "notified_to": f"{complaint.get('assigned_staff_name') or 'Field Staff'} & Dept Supervisor",
            "channel": "in_app",
            "action_taken": f"Urgent 18-hour reminder sent. Only {int(sla_hrs - elapsed_hours)} hours before SLA breach.",
            "escalation_title": "Urgent Supervisor Alert",
            "target_role": "supervisor",
        })

    # 3. 22 Hours: Final Warning
    if elapsed_hours >= r3_hrs and "final_warning_22h" not in existing_levels:
        new_actions.append({
            "level": "final_warning_22h",
            "notified_to": "Department Manager & Control Room",
            "channel": "in_app",
            "action_taken": "Final 22-hour warning triggered. High risk of imminent SLA breach.",
            "escalation_title": "Final Warning: 2 Hours Remaining",
            "target_role": "manager",
        })

    # 4. 24 Hours: SLA Breached Escalation
    if elapsed_hours >= sla_hrs and "breach_24h" not in existing_levels:
        new_actions.append({
            "level": "breach_24h",
            "notified_to": "Control Room & Executive Director",
            "channel": "urgent_notification",
            "action_taken": "SLA BREACHED: Escalated to Control Room and Senior Management for immediate intervention.",
            "escalation_title": "SLA BREACHED — Senior Intervention Required",
            "target_role": "control_room",
            "set_status": "ESCALATED",
        })

    return new_actions
