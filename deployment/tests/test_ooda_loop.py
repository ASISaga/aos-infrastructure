"""Tests for the OODA loop closed-loop orchestration pattern.

Validates the Observe→Orient→Decide→Act cycle with various infrastructure
states, cost thresholds, and desired-state configurations.
"""

from __future__ import annotations

from unittest import mock

import pytest

from orchestrator.core.ooda_loop import (
    ActionResult,
    Decision,
    DesiredState,
    HealthAssessment,
    OODACycle,
    OODALoop,
    Observation,
    Orientation,
    RecommendedAction,
)
from orchestrator.integration.azure_sdk_client import (
    AzureSDKClient,
    CostSummary,
    InfrastructureSnapshot,
    ProvisioningState,
    ResourceState,
)


# ====================================================================
# Helpers
# ====================================================================


def _resource(name: str, state: ProvisioningState = ProvisioningState.SUCCEEDED) -> ResourceState:
    return ResourceState(
        name=name,
        resource_type="Microsoft.Storage/storageAccounts",
        location="eastus",
        provisioning_state=state,
    )


def _snapshot(
    resources: list[ResourceState] | None = None,
    cost: CostSummary | None = None,
) -> InfrastructureSnapshot:
    return InfrastructureSnapshot(
        resource_group="rg-test",
        timestamp="2026-01-01T00:00:00Z",
        resources=resources or [],
        cost=cost,
    )


def _mock_client(snapshot: InfrastructureSnapshot) -> mock.MagicMock:
    client = mock.MagicMock(spec=AzureSDKClient)
    client.observe.return_value = snapshot
    return client


# ====================================================================
# Observation tests
# ====================================================================


class TestObservation:
    """Tests for the Observe phase."""

    def test_observation_timestamp(self) -> None:
        snap = _snapshot()
        obs = Observation(snapshot=snap)
        assert obs.timestamp == snap.timestamp

    def test_observation_to_dict(self) -> None:
        snap = _snapshot(resources=[_resource("a")])
        obs = Observation(snapshot=snap)
        d = obs.to_dict()
        assert d["total_resources"] == 1
        assert d["healthy_resources"] == 1


# ====================================================================
# Orientation tests
# ====================================================================


class TestOrientation:
    """Tests for the Orient phase."""

    def test_healthy_no_drift(self) -> None:
        snap = _snapshot(resources=[_resource("a"), _resource("b")])
        desired = DesiredState(expected_resources=[
            {"name": "a", "type": "T"},
            {"name": "b", "type": "T"},
        ])
        loop = OODALoop(client=_mock_client(snap), desired_state=desired)
        obs = Observation(snapshot=snap)
        ori = loop.orient(obs)
        assert ori.health == HealthAssessment.HEALTHY
        assert ori.drift_detected is False
        assert ori.state_matches_desired is True

    def test_missing_resources_detected(self) -> None:
        snap = _snapshot(resources=[_resource("a")])
        desired = DesiredState(expected_resources=[
            {"name": "a", "type": "T"},
            {"name": "b", "type": "T"},
        ])
        loop = OODALoop(client=_mock_client(snap), desired_state=desired)
        obs = Observation(snapshot=snap)
        ori = loop.orient(obs)
        assert ori.drift_detected is True
        assert "b" in ori.missing_resources
        assert ori.state_matches_desired is False

    def test_unhealthy_resources_detected(self) -> None:
        snap = _snapshot(resources=[
            _resource("a", ProvisioningState.SUCCEEDED),
            _resource("b", ProvisioningState.FAILED),
        ])
        desired = DesiredState(expected_resources=[
            {"name": "a", "type": "T"},
            {"name": "b", "type": "T"},
        ])
        loop = OODALoop(client=_mock_client(snap), desired_state=desired)
        obs = Observation(snapshot=snap)
        ori = loop.orient(obs)
        assert ori.health == HealthAssessment.DEGRADED
        assert "b" in ori.unhealthy_resources
        assert ori.drift_detected is True

    def test_cost_over_threshold(self) -> None:
        cost = CostSummary(total_cost=600.0, currency="USD")
        snap = _snapshot(resources=[_resource("a")], cost=cost)
        desired = DesiredState(expected_resources=[{"name": "a", "type": "T"}])
        loop = OODALoop(
            client=_mock_client(snap),
            desired_state=desired,
            cost_threshold=500.0,
        )
        obs = Observation(snapshot=snap)
        ori = loop.orient(obs)
        assert ori.cost_within_budget is False
        assert ori.current_cost == 600.0
        assert ori.cost_threshold == 500.0

    def test_cost_within_threshold(self) -> None:
        cost = CostSummary(total_cost=300.0, currency="USD")
        snap = _snapshot(resources=[_resource("a")], cost=cost)
        desired = DesiredState(expected_resources=[{"name": "a", "type": "T"}])
        loop = OODALoop(
            client=_mock_client(snap),
            desired_state=desired,
            cost_threshold=500.0,
        )
        obs = Observation(snapshot=snap)
        ori = loop.orient(obs)
        assert ori.cost_within_budget is True

    def test_no_cost_threshold_always_within_budget(self) -> None:
        cost = CostSummary(total_cost=99999.0)
        snap = _snapshot(resources=[_resource("a")], cost=cost)
        loop = OODALoop(client=_mock_client(snap), cost_threshold=0)
        obs = Observation(snapshot=snap)
        ori = loop.orient(obs)
        assert ori.cost_within_budget is True

    def test_empty_expected_no_unexpected(self) -> None:
        """When no expected resources specified, don't flag observed ones as unexpected."""
        snap = _snapshot(resources=[_resource("a")])
        loop = OODALoop(client=_mock_client(snap), desired_state=DesiredState())
        obs = Observation(snapshot=snap)
        ori = loop.orient(obs)
        assert ori.unexpected_resources == []

    def test_unknown_health_no_resources(self) -> None:
        snap = _snapshot(resources=[])
        loop = OODALoop(client=_mock_client(snap))
        obs = Observation(snapshot=snap)
        ori = loop.orient(obs)
        assert ori.health == HealthAssessment.UNKNOWN

    def test_all_unhealthy(self) -> None:
        snap = _snapshot(resources=[
            _resource("a", ProvisioningState.FAILED),
            _resource("b", ProvisioningState.CANCELED),
        ])
        loop = OODALoop(client=_mock_client(snap))
        obs = Observation(snapshot=snap)
        ori = loop.orient(obs)
        assert ori.health == HealthAssessment.UNHEALTHY


# ====================================================================
# Decision tests
# ====================================================================


class TestDecision:
    """Tests for the Decide phase."""

    def test_decide_skip_when_state_matches(self) -> None:
        ori = Orientation(
            health=HealthAssessment.HEALTHY,
            state_matches_desired=True,
            cost_within_budget=True,
        )
        loop = OODALoop(client=_mock_client(_snapshot()))
        dec = loop.decide(ori)
        assert dec.recommended_action == RecommendedAction.SKIP
        assert dec.approved is True

    def test_decide_deploy_all_missing(self) -> None:
        ori = Orientation(
            health=HealthAssessment.UNKNOWN,
            missing_resources=["a", "b"],
            state_matches_desired=False,
            cost_within_budget=True,
        )
        loop = OODALoop(client=_mock_client(_snapshot()))
        loop.desired_state = DesiredState(expected_resources=[
            {"name": "a", "type": "T"},
            {"name": "b", "type": "T"},
        ])
        dec = loop.decide(ori)
        assert dec.recommended_action == RecommendedAction.DEPLOY
        assert dec.approved is False  # Full deploy requires approval

    def test_decide_incremental_partial_missing(self) -> None:
        ori = Orientation(
            health=HealthAssessment.HEALTHY,
            missing_resources=["b"],
            state_matches_desired=False,
            cost_within_budget=True,
        )
        loop = OODALoop(client=_mock_client(_snapshot()))
        loop.desired_state = DesiredState(expected_resources=[
            {"name": "a", "type": "T"},
            {"name": "b", "type": "T"},
        ])
        dec = loop.decide(ori)
        assert dec.recommended_action == RecommendedAction.INCREMENTAL_UPDATE
        assert "b" in dec.target_resources

    def test_decide_remediate_unhealthy(self) -> None:
        ori = Orientation(
            health=HealthAssessment.DEGRADED,
            unhealthy_resources=["b"],
            state_matches_desired=False,
            cost_within_budget=True,
        )
        loop = OODALoop(client=_mock_client(_snapshot()))
        dec = loop.decide(ori)
        assert dec.recommended_action == RecommendedAction.REMEDIATE
        assert "b" in dec.target_resources

    def test_decide_scale_down_cost_exceeded(self) -> None:
        ori = Orientation(
            health=HealthAssessment.HEALTHY,
            state_matches_desired=True,
            cost_within_budget=False,
            current_cost=800.0,
            cost_threshold=500.0,
        )
        loop = OODALoop(client=_mock_client(_snapshot()))
        dec = loop.decide(ori)
        assert dec.recommended_action == RecommendedAction.SCALE_DOWN
        assert dec.approved is False  # Always requires manual approval

    def test_decide_alert_degraded_no_missing(self) -> None:
        ori = Orientation(
            health=HealthAssessment.DEGRADED,
            state_matches_desired=False,
            cost_within_budget=True,
        )
        loop = OODALoop(client=_mock_client(_snapshot()))
        dec = loop.decide(ori)
        assert dec.recommended_action == RecommendedAction.ALERT
        assert dec.approved is True

    def test_auto_approve_incremental(self) -> None:
        ori = Orientation(
            health=HealthAssessment.HEALTHY,
            missing_resources=["b"],
            state_matches_desired=False,
            cost_within_budget=True,
        )
        loop = OODALoop(client=_mock_client(_snapshot()), auto_approve=True)
        loop.desired_state = DesiredState(expected_resources=[
            {"name": "a", "type": "T"},
            {"name": "b", "type": "T"},
        ])
        dec = loop.decide(ori)
        assert dec.approved is True


# ====================================================================
# Action tests
# ====================================================================


class TestAction:
    """Tests for the Act phase."""

    def test_act_skip_succeeds(self) -> None:
        dec = Decision(
            recommended_action=RecommendedAction.SKIP,
            rationale="all good",
            approved=True,
        )
        loop = OODALoop(client=_mock_client(_snapshot()))
        result = loop.act(dec)
        assert result.success is True
        assert result.action == RecommendedAction.SKIP

    def test_act_not_approved(self) -> None:
        dec = Decision(
            recommended_action=RecommendedAction.DEPLOY,
            rationale="full deploy",
            approved=False,
        )
        loop = OODALoop(client=_mock_client(_snapshot()))
        result = loop.act(dec)
        assert result.success is False
        assert "not approved" in result.details

    def test_act_alert(self) -> None:
        dec = Decision(
            recommended_action=RecommendedAction.ALERT,
            rationale="degraded",
            approved=True,
        )
        loop = OODALoop(client=_mock_client(_snapshot()))
        result = loop.act(dec)
        assert result.success is True
        assert "Alert raised" in result.details

    def test_act_deploy_initiated(self) -> None:
        dec = Decision(
            recommended_action=RecommendedAction.DEPLOY,
            rationale="resources missing",
            target_resources=["a", "b"],
            approved=True,
        )
        loop = OODALoop(client=_mock_client(_snapshot()))
        result = loop.act(dec)
        assert result.success is True
        assert result.resources_affected == 2


# ====================================================================
# Full cycle tests
# ====================================================================


class TestOODACycle:
    """Tests for the complete OODA cycle."""

    def test_run_cycle_skip(self) -> None:
        """When desired state already achieved, cycle should recommend SKIP."""
        snap = _snapshot(resources=[_resource("a")])
        desired = DesiredState(expected_resources=[{"name": "a", "type": "T"}])
        client = _mock_client(snap)
        loop = OODALoop(client=client, desired_state=desired)
        cycle = loop.run_cycle()
        assert cycle.decision.recommended_action == RecommendedAction.SKIP
        assert cycle.action_result is not None
        assert cycle.action_result.success is True

    def test_run_cycle_deploy_not_approved(self) -> None:
        """Full deploy should NOT be auto-approved (safety check)."""
        snap = _snapshot(resources=[])
        desired = DesiredState(expected_resources=[
            {"name": "a", "type": "T"},
            {"name": "b", "type": "T"},
        ])
        client = _mock_client(snap)
        loop = OODALoop(client=client, desired_state=desired, auto_approve=False)
        cycle = loop.run_cycle()
        assert cycle.decision.recommended_action == RecommendedAction.DEPLOY
        assert cycle.decision.approved is False
        # Action should NOT have run (no action_result populated)
        assert cycle.action_result is None

    def test_run_cycle_cost_blocks_deploy(self) -> None:
        cost = CostSummary(total_cost=1000.0)
        snap = _snapshot(resources=[_resource("a")], cost=cost)
        desired = DesiredState(expected_resources=[{"name": "a", "type": "T"}])
        client = _mock_client(snap)
        loop = OODALoop(
            client=client, desired_state=desired, cost_threshold=500.0,
        )
        cycle = loop.run_cycle(include_cost=True)
        assert cycle.decision.recommended_action == RecommendedAction.SCALE_DOWN
        assert cycle.decision.approved is False

    def test_cycle_history_tracked(self) -> None:
        snap = _snapshot(resources=[_resource("a")])
        client = _mock_client(snap)
        loop = OODALoop(client=client)
        loop.run_cycle()
        loop.run_cycle()
        assert len(loop.cycles) == 2

    def test_approve_action_manually(self) -> None:
        """Test that a pending action can be manually approved."""
        snap = _snapshot(resources=[])
        desired = DesiredState(expected_resources=[
            {"name": "a", "type": "T"},
            {"name": "b", "type": "T"},
        ])
        client = _mock_client(snap)
        loop = OODALoop(client=client, desired_state=desired)
        cycle = loop.run_cycle()
        assert cycle.decision.approved is False

        loop.approve_action(cycle)
        assert cycle.decision.approved is True

    def test_format_cycle_report(self) -> None:
        snap = _snapshot(resources=[_resource("a")])
        desired = DesiredState(expected_resources=[{"name": "a", "type": "T"}])
        client = _mock_client(snap)
        loop = OODALoop(client=client, desired_state=desired)
        cycle = loop.run_cycle()
        report = loop.format_cycle_report(cycle)
        assert "OODA CYCLE REPORT" in report
        assert "OBSERVE" in report
        assert "ORIENT" in report
        assert "DECIDE" in report

    def test_cycle_to_dict(self) -> None:
        snap = _snapshot(resources=[_resource("a")])
        client = _mock_client(snap)
        loop = OODALoop(client=client)
        cycle = loop.run_cycle()
        d = cycle.to_dict()
        assert "observation" in d
        assert "orientation" in d
        assert "decision" in d


# ====================================================================
# Data model serialization tests
# ====================================================================


class TestSerialization:
    """Test to_dict() for all OODA data models."""

    def test_orientation_to_dict(self) -> None:
        ori = Orientation(
            health=HealthAssessment.HEALTHY,
            drift_detected=False,
            cost_within_budget=True,
            current_cost=100.0,
            cost_threshold=500.0,
            state_matches_desired=True,
        )
        d = ori.to_dict()
        assert d["health"] == "healthy"
        assert d["cost_within_budget"] is True

    def test_decision_to_dict(self) -> None:
        dec = Decision(
            recommended_action=RecommendedAction.DEPLOY,
            rationale="test",
            target_resources=["a"],
            approved=False,
        )
        d = dec.to_dict()
        assert d["recommended_action"] == "deploy"
        assert d["approved"] is False

    def test_action_result_to_dict(self) -> None:
        ar = ActionResult(
            action=RecommendedAction.SKIP,
            success=True,
            details="no action needed",
        )
        d = ar.to_dict()
        assert d["action"] == "skip"
        assert d["success"] is True
