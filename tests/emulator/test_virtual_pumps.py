import pytest

from agzs_shared.enums import ObservationQuality, PumpStatus
from agzs_shared.money import cost_kzt_test_half_up, max_ml_for_prepaid
from agzs_station.errors import UnknownPumpAddress
from agzs_station.pumps.adapters.emulator import EmulatorAdapter, ManualClock
from agzs_station.pumps.telemetry import AdapterOutcome


def _adapter(count: int = 2, flow: int = 1000, price: int = 100) -> tuple[EmulatorAdapter, ManualClock]:
    clock = ManualClock()
    adapter = EmulatorAdapter(
        pump_count=count,
        flow_ml_per_s=flow,
        price_kzt_per_liter=price,
        clock=clock,
    )
    return adapter, clock


def test_two_pumps_dispense_together_and_stop_at_their_own_sums() -> None:
    adapter, clock = _adapter()
    first = adapter.authorize("1", "dose-1", 10)
    second = adapter.authorize("2", "dose-2", 25)
    assert first.outcome is AdapterOutcome.ACKNOWLEDGED
    assert second.outcome is AdapterOutcome.ACKNOWLEDGED
    assert first.totalizer_start_ml == 0
    assert second.totalizer_start_ml == 0

    clock.advance_ms(50)
    left = adapter.read_status("1")
    right = adapter.read_status("2")
    assert left.status is PumpStatus.DISPENSING
    assert right.status is PumpStatus.DISPENSING
    assert left.observation_quality is ObservationQuality.CONFIRMED
    assert left.dose_ml == 50
    assert right.dose_ml == 50

    clock.advance_ms(5_000)
    left = adapter.read_status("1")
    right = adapter.read_status("2")
    left_cap = max_ml_for_prepaid(10, 100)
    right_cap = max_ml_for_prepaid(25, 100)
    assert left.status is PumpStatus.COMPLETED
    assert right.status is PumpStatus.COMPLETED
    assert left.dose_ml == left_cap
    assert right.dose_ml == right_cap
    assert cost_kzt_test_half_up(left_cap, 100) == 10
    assert cost_kzt_test_half_up(right_cap, 100) == 25
    assert cost_kzt_test_half_up(left_cap + 1, 100) == 11
    assert adapter.read_totalizer("1").total_ml == left_cap
    assert adapter.read_totalizer("2").total_ml == right_cap


def test_link_break_hides_state_until_the_link_returns() -> None:
    adapter, clock = _adapter(count=1, flow=1000)
    adapter.authorize("1", "dose", 50)
    clock.advance_ms(20)
    adapter.cut_link()
    hidden = adapter.read_status("1")
    assert hidden.observation_quality is ObservationQuality.UNKNOWN
    assert hidden.status is PumpStatus.OFFLINE
    assert hidden.dose_ml is None
    assert adapter.read_totalizer("1").total_ml is None
    assert adapter.probe().up is False

    clock.advance_ms(30)
    adapter.restore_link()
    visible = adapter.read_status("1")
    assert visible.observation_quality is ObservationQuality.CONFIRMED
    assert visible.status is PumpStatus.DISPENSING
    assert visible.dose_ml == 50


def test_one_pump_can_drop_out_while_the_other_keeps_dispensing() -> None:
    adapter, clock = _adapter()
    adapter.authorize("1", "a", 20)
    adapter.authorize("2", "b", 20)
    adapter.cut_pump("1")
    clock.advance_ms(10)
    hidden = adapter.read_status("1")
    visible = adapter.read_status("2")
    assert adapter.probe().up is True
    assert hidden.observation_quality is ObservationQuality.UNKNOWN
    assert visible.status is PumpStatus.DISPENSING
    assert visible.dose_ml == 10
    adapter.restore_pump("1")
    assert adapter.read_status("1").dose_ml == 10


def test_early_stop_and_fault_freeze_the_totalizer() -> None:
    adapter, clock = _adapter(count=1, flow=1000)
    adapter.authorize("1", "dose", 100)
    clock.advance_ms(15)
    adapter.stop_early("1")
    clock.advance_ms(1_000)
    stopped = adapter.read_status("1")
    assert stopped.status is PumpStatus.STOPPED
    assert stopped.dose_ml == 15
    adapter.release("1")

    adapter.authorize("1", "next", 100)
    clock.advance_ms(10)
    adapter.inject_fault("1", "VALVE")
    clock.advance_ms(1_000)
    failed = adapter.read_status("1")
    assert failed.status is PumpStatus.ERROR
    assert failed.fault_code == "VALVE"
    assert failed.observation_quality is ObservationQuality.CONFIRMED
    assert failed.dose_ml == 10
    adapter.clear_fault("1")
    assert adapter.read_status("1").status is PumpStatus.STOPPED


def test_repeated_command_does_not_start_a_second_dose() -> None:
    adapter, clock = _adapter(count=1, flow=1000)
    first = adapter.authorize("1", "same", 10)
    again = adapter.authorize("1", "same", 10)
    assert again == first
    clock.advance_ms(1_000)
    cap = max_ml_for_prepaid(10, 100)
    assert adapter.read_status("1").dose_ml == cap
    assert adapter.read_totalizer("1").total_ml == cap


def test_authorize_during_outage_stays_unknown_and_does_not_flow() -> None:
    adapter, clock = _adapter(count=1, flow=1000)
    adapter.cut_link()
    result = adapter.authorize("1", "lost", 10)
    assert result.outcome is AdapterOutcome.UNKNOWN
    assert adapter.read_authorization("lost").outcome is AdapterOutcome.UNKNOWN
    clock.advance_ms(1_000)
    adapter.restore_link()
    status = adapter.read_status("1")
    assert status.status is PumpStatus.IDLE
    assert status.totalizer_ml == 0


def test_power_off_is_a_known_offline_state() -> None:
    adapter, _clock = _adapter(count=1)
    adapter.set_powered("1", False)
    status = adapter.read_status("1")
    assert status.status is PumpStatus.OFFLINE
    assert status.observation_quality is ObservationQuality.CONFIRMED
    assert adapter.read_totalizer("1").total_ml == 0


def test_unknown_address_and_pump_count_are_rejected() -> None:
    adapter, _clock = _adapter(count=1)
    with pytest.raises(UnknownPumpAddress):
        adapter.read_status("9")
    with pytest.raises(ValueError):
        EmulatorAdapter(pump_count=0)
    with pytest.raises(ValueError):
        EmulatorAdapter(pump_count=7)


def test_second_command_while_dispensing_is_rejected() -> None:
    adapter, _clock = _adapter(count=1, flow=1000)
    adapter.authorize("1", "first", 50)
    second = adapter.authorize("1", "second", 50)
    assert second.outcome is AdapterOutcome.REJECTED
    assert adapter.read_authorization("first").outcome is AdapterOutcome.ACKNOWLEDGED
