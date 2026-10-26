# retail/actions.py
# The "advice that checks itself" loop: store the actions the AI suggested, let the owner
# mark which ones they did, then measure whether each one actually worked.
import json
import uuid
from pathlib import Path

import pandas as pd

from retail.data import in_period

STORE = Path(__file__).resolve().parent.parent / "actions.json"
METRIC_COLUMNS = {"revenue": "REVENUE", "units": "QUANTITY", "profit": "PROFIT"}


def load_actions() -> list:
    if not STORE.exists():
        return []
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_actions(actions: list) -> None:
    STORE.write_text(json.dumps(actions, indent=2, default=str), encoding="utf-8")


def add_actions(new_actions: list, start_date) -> None:
    actions = load_actions()
    for a in new_actions:
        actions.append({
            **a,
            "id": uuid.uuid4().hex[:8],
            "start_date": pd.Timestamp(start_date).date().isoformat(),
            "status": "suggested",  # suggested -> done | skipped
            "result": None,
        })
    save_actions(actions)


def update_action(action_id: str, **fields) -> None:
    actions = load_actions()
    for a in actions:
        if a["id"] == action_id:
            a.update(fields)
    save_actions(actions)


def _target_mask(df: pd.DataFrame, action: dict) -> pd.Series:
    if action.get("target_product") and action["target_product"] in set(df['PRODUCT']):
        return df['PRODUCT'] == action["target_product"]
    if action.get("target_category") and action["target_category"] in set(df['CATEGORY']):
        return df['CATEGORY'] == action["target_category"]
    return pd.Series(True, index=df.index)


def evaluate_action(df: pd.DataFrame, action: dict) -> dict:
    """Compare the target's metric in the window after the action with the same-length window
    before it, and subtract the change in the rest of the shop over the same time. That removes
    shop-wide swings (seasons, holidays) that would happen anyway."""
    start = pd.Timestamp(action["start_date"])
    days = int(action.get("measure_days") or 14)
    after_end = start + pd.Timedelta(days=days - 1)
    before_start = start - pd.Timedelta(days=days)
    if df['DATE'].max() < after_end:
        return {"state": "waiting",
                "message": f"Needs sales data up to {after_end.date()} (data ends {df['DATE'].max().date()})."}

    col = METRIC_COLUMNS.get(action.get("metric"), "REVENUE")
    mask = _target_mask(df, action)
    target, rest = df[mask], df[~mask]

    def change(part):
        before = in_period(part, before_start, start - pd.Timedelta(days=1))[col].sum()
        after = in_period(part, start, after_end)[col].sum()
        return before, after, ((after - before) / before * 100) if before else None

    t_before, t_after, t_change = change(target)
    _, _, r_change = change(rest) if not rest.empty else (0, 0, 0.0)
    if t_change is None:
        return {"state": "no_baseline", "message": "The target had no sales before the action, so there is nothing to compare."}

    net = t_change - (r_change or 0)
    expected = float(action.get("expected_uplift_pct") or 0)
    worked = net > 0 and (expected <= 0 or net >= expected / 2)
    return {
        "state": "measured",
        "metric": action.get("metric", "revenue"),
        "before": round(float(t_before), 2),
        "after": round(float(t_after), 2),
        "target_change_pct": round(t_change, 1),
        "rest_of_shop_change_pct": round(r_change, 1) if r_change is not None else None,
        "net_effect_pct": round(net, 1),
        "expected_pct": expected,
        "verdict": "worked" if worked else "did not work",
    }


def track_record(actions: list) -> dict:
    """Per action type: how many done actions were measured and how many worked."""
    record = {}
    for a in actions:
        r = a.get("result") or {}
        if a.get("status") != "done" or r.get("state") != "measured":
            continue
        t = record.setdefault(a["action_type"], {"measured": 0, "worked": 0, "net_effects_pct": []})
        t["measured"] += 1
        t["worked"] += r["verdict"] == "worked"
        t["net_effects_pct"].append(r["net_effect_pct"])
    for t in record.values():
        t["avg_net_effect_pct"] = round(sum(t["net_effects_pct"]) / len(t["net_effects_pct"]), 1)
    return record
