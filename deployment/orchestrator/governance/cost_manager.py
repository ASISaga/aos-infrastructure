"""Governance pillar — Azure Cost Management and budget enforcement.

``CostManager`` retrieves current spend, forecasts, and manages budget
alerts for AOS resource groups.  It uses the Azure Cost Management SDK
(``azure-mgmt-costmanagement``) via :class:`AzureSDKClient` for type-safe,
closed-loop cost queries.
"""

from __future__ import annotations

import logging
from typing import Any

from orchestrator.integration.azure_sdk_client import AzureSDKClient

logger = logging.getLogger(__name__)

# Default budget thresholds (% of limit) that trigger alerts
_DEFAULT_ALERT_THRESHOLDS: list[int] = [50, 80, 100]


class CostManager:
    """Manages Azure cost visibility and budget enforcement for AOS.

    Uses :class:`AzureSDKClient` for structured cost data via the Azure
    Cost Management Query API.
    """

    def __init__(self, resource_group: str, subscription_id: str) -> None:
        self.resource_group = resource_group
        self.subscription_id = subscription_id
        self._client = AzureSDKClient(subscription_id, resource_group)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_current_spend(self, period_days: int = 30) -> dict[str, Any]:
        """Return the current cost summary for the resource group.

        Uses the Azure Cost Management SDK for type-safe cost queries.

        Parameters
        ----------
        period_days:
            Number of days to look back (default: 30).

        Returns a dict with keys:
        - ``currency``: currency code (e.g. "USD")
        - ``total_cost``: total cost as float
        - ``period_start``: start date of the query
        - ``period_end``: end date of the query
        - ``by_service``: list of ``{service, cost}`` breakdowns
        """
        print(f"💰 Fetching cost data for {self.resource_group} (last {period_days}d)")
        cost = self._client.get_current_cost(period_days)
        summary = cost.to_dict()
        self._print_cost_summary(summary)
        return summary

    def create_budget(
        self,
        name: str,
        amount: float,
        environment: str,
        thresholds: list[int] | None = None,
        contact_emails: list[str] | None = None,
    ) -> bool:
        """Create or update an Azure Cost Management budget with alert notifications.

        Parameters
        ----------
        name:
            Unique budget name.
        amount:
            Budget limit in the subscription's currency.
        environment:
            Deployment environment (``dev``, ``staging``, ``prod``).
        thresholds:
            Alert thresholds as percentages (default: 50, 80, 100).
        contact_emails:
            Email addresses to notify on threshold breach.
        """
        from datetime import date

        print(f"📊 Creating budget '{name}' (${amount:,.2f}) for {self.resource_group}")
        thresholds = thresholds or _DEFAULT_ALERT_THRESHOLDS
        contact_emails = contact_emails or []

        try:
            from azure.mgmt.costmanagement import CostManagementClient  # type: ignore[import]
            from azure.identity import DefaultAzureCredential  # type: ignore[import]

            credential = DefaultAzureCredential()
            cost_client = CostManagementClient(credential)

            today = date.today()
            start_date = today.replace(day=1).isoformat() + "T00:00:00Z"
            end_date = today.replace(month=12, day=31).isoformat() + "T23:59:59Z"

            scope = (
                f"/subscriptions/{self.subscription_id}"
                f"/resourceGroups/{self.resource_group}"
            )
            budget_name = f"aos-{name}-{environment}"

            notifications: dict[str, Any] = {}
            for i, threshold_pct in enumerate(thresholds):
                key = f"alert_{threshold_pct}"
                notif: dict[str, Any] = {
                    "enabled": True,
                    "operator": "GreaterThanOrEqualTo",
                    "threshold": threshold_pct,
                    "thresholdType": "Actual",
                    "contactEmails": contact_emails,
                }
                notifications[key] = notif

            budget_body = {
                "category": "Cost",
                "amount": amount,
                "timeGrain": "Monthly",
                "timePeriod": {
                    "startDate": start_date,
                    "endDate": end_date,
                },
                "notifications": notifications,
            }

            cost_client.budgets.create_or_update(scope, budget_name, budget_body)
            print(f"  ✅ Budget '{budget_name}': created")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Budget creation failed: %s", exc)
            print(f"  ⚠️ Budget '{name}': failed — {exc}")
            return False

    def list_budgets(self) -> list[dict[str, Any]]:
        """Return all budgets associated with the resource group."""
        try:
            from azure.mgmt.costmanagement import CostManagementClient  # type: ignore[import]
            from azure.identity import DefaultAzureCredential  # type: ignore[import]

            credential = DefaultAzureCredential()
            cost_client = CostManagementClient(credential)

            scope = (
                f"/subscriptions/{self.subscription_id}"
                f"/resourceGroups/{self.resource_group}"
            )

            budgets_result = cost_client.budgets.list(scope)
            budgets: list[dict[str, Any]] = []
            for b in budgets_result:
                amount = b.amount or 0
                current = b.current_spend.amount if b.current_spend else 0
                name = b.name or "N/A"
                pct = round(100 * float(current) / float(amount), 1) if amount else 0
                print(f"  💰 {name}: ${current:,.2f} / ${amount:,.2f} ({pct}%)")
                budgets.append({
                    "name": name,
                    "amount": float(amount),
                    "currentSpend": {"amount": float(current)},
                })
            return budgets
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to list budgets: %s", exc)
            return []

    def check_budget_alerts(self) -> list[str]:
        """Return a list of budget names that have exceeded their alert thresholds."""
        budgets = self.list_budgets()
        alerts: list[str] = []
        for b in budgets:
            amount = float(b.get("amount", 0))
            current = float(b.get("currentSpend", {}).get("amount", 0))
            if amount > 0 and (current / amount) >= 0.80:
                alerts.append(b.get("name", "unknown"))
        if alerts:
            print(f"  ⚠️  Budgets exceeding 80% threshold: {', '.join(alerts)}")
        else:
            print("  ✅ All budgets within thresholds")
        return alerts

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _print_cost_summary(summary: dict[str, Any]) -> None:
        currency = summary["currency"]
        total = summary["total_cost"]
        start = summary["period_start"]
        end = summary["period_end"]
        print(f"\n  Total spend ({start} → {end}): {currency} {total:,.4f}")
        for svc in summary["by_service"][:10]:
            print(f"    {svc['service']}: {currency} {svc['cost']:,.4f}")
