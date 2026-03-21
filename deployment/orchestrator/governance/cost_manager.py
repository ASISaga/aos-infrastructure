"""Governance pillar — Azure Cost Management and budget enforcement.

``CostManager`` retrieves current spend, forecasts, and manages budget
alerts for AOS resource groups.  It preferentially uses the Azure Cost
Management SDK (``azure-mgmt-costmanagement``) for type-safe, closed-loop
cost queries.  When the SDK is not installed it falls back to ``az
consumption`` CLI commands.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Default budget thresholds (% of limit) that trigger alerts
_DEFAULT_ALERT_THRESHOLDS: list[int] = [50, 80, 100]


class CostManager:
    """Manages Azure cost visibility and budget enforcement for AOS.

    Supports two execution modes:

    * **SDK mode** — uses :class:`AzureSDKClient` when available, providing
      structured cost data via the Azure Cost Management Query API.
    * **CLI mode** — falls back to ``az consumption`` subprocess calls.
    """

    def __init__(self, resource_group: str, subscription_id: str = "") -> None:
        self.resource_group = resource_group
        self.subscription_id = subscription_id
        self._sdk_client: Any = None
        self._init_sdk_client()

    def _init_sdk_client(self) -> None:
        """Attempt to initialise the Azure SDK client for cost queries."""
        try:
            from orchestrator.integration.azure_sdk_client import AzureSDKClient
            if self.subscription_id:
                client = AzureSDKClient.create(self.subscription_id, self.resource_group)
                if client.sdk_available:
                    self._sdk_client = client
                    logger.info("CostManager: using Azure SDK for cost queries")
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_current_spend(self, period_days: int = 30) -> dict[str, Any]:
        """Return the current cost summary for the resource group.

        Preferentially uses the Azure Cost Management SDK (closed-loop) and
        falls back to ``az consumption usage list`` when unavailable.

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

        # Try SDK-based cost query first (closed-loop)
        if self._sdk_client is not None:
            try:
                cost = self._sdk_client.get_current_cost(period_days)
                summary = cost.to_dict()
                self._print_cost_summary(summary)
                return summary
            except Exception as exc:  # noqa: BLE001
                logger.warning("SDK cost query failed, falling back to CLI: %s", exc)

        # CLI fallback (open-loop)
        end = date.today()
        start = end - timedelta(days=period_days)

        result = self._az([
            "consumption", "usage", "list",
            "--start-date", start.isoformat(),
            "--end-date", end.isoformat(),
            "--output", "json",
        ])
        if result is None:
            return {
                "currency": "USD",
                "total_cost": 0.0,
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
                "by_service": [],
            }

        usage: list[dict[str, Any]] = json.loads(result)
        # Filter to resource group
        rg_lower = self.resource_group.lower()
        rg_usage = [
            u for u in usage
            if rg_lower in (u.get("instanceId") or "").lower()
        ]

        total = sum(float(u.get("pretaxCost", 0)) for u in rg_usage)
        currency = rg_usage[0].get("currency", "USD") if rg_usage else "USD"

        # Group by service
        service_costs: dict[str, float] = {}
        for u in rg_usage:
            svc = u.get("meterCategory", "Other")
            service_costs[svc] = service_costs.get(svc, 0.0) + float(u.get("pretaxCost", 0))
        by_service = [
            {"service": svc, "cost": round(cost, 4)}
            for svc, cost in sorted(service_costs.items(), key=lambda x: -x[1])
        ]

        summary = {
            "currency": currency,
            "total_cost": round(total, 4),
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "by_service": by_service,
        }
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
        print(f"📊 Creating budget '{name}' (${amount:,.2f}) for {self.resource_group}")
        thresholds = thresholds or _DEFAULT_ALERT_THRESHOLDS
        contact_emails = contact_emails or []

        # Build budget period (current month to end of year)
        today = date.today()
        start = today.replace(day=1).isoformat()
        end = today.replace(month=12, day=31).isoformat()

        cmd = [
            "az", "consumption", "budget", "create",
            "--budget-name", f"aos-{name}-{environment}",
            "--amount", str(amount),
            "--time-grain", "Monthly",
            "--start-date", start,
            "--end-date", end,
            "--resource-group", self.resource_group,
            "--output", "json",
        ]
        if contact_emails:
            cmd += ["--contact-emails"] + contact_emails
        if thresholds:
            cmd += ["--thresholds"] + [str(t) for t in thresholds]

        result = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
        ok = result.returncode == 0
        icon = "✅" if ok else "⚠️"
        print(f"  {icon} Budget '{name}': {'created' if ok else 'failed'}")
        return ok

    def list_budgets(self) -> list[dict[str, Any]]:
        """Return all budgets associated with the resource group."""
        result = self._az([
            "consumption", "budget", "list",
            "--resource-group", self.resource_group,
            "--output", "json",
        ])
        if result is None:
            return []
        budgets: list[dict[str, Any]] = json.loads(result)
        for b in budgets:
            amount = b.get("amount", 0)
            current = b.get("currentSpend", {}).get("amount", 0)
            name = b.get("name", "N/A")
            pct = round(100 * float(current) / float(amount), 1) if amount else 0
            print(f"  💰 {name}: ${current:,.2f} / ${amount:,.2f} ({pct}%)")
        return budgets

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

    def _az(self, args: list[str]) -> str | None:
        """Run an ``az`` command and return stdout, or *None* on failure."""
        result = subprocess.run(["az"] + args, capture_output=True, text=True)  # noqa: S603
        if result.returncode != 0:
            print(f"  az command failed (rc={result.returncode})", file=sys.stderr)
            if result.stderr:
                print(f"  {result.stderr.strip()}", file=sys.stderr)
            return None
        return result.stdout

    @staticmethod
    def _print_cost_summary(summary: dict[str, Any]) -> None:
        currency = summary["currency"]
        total = summary["total_cost"]
        start = summary["period_start"]
        end = summary["period_end"]
        print(f"\n  Total spend ({start} → {end}): {currency} {total:,.4f}")
        for svc in summary["by_service"][:10]:
            print(f"    {svc['service']}: {currency} {svc['cost']:,.4f}")
