"""scripts/refresh_exchange_rates.py and its weekly workflow job (v1.76.0).

All offline: payloads are fixtures parsed via --from-file or the parse_*
functions; the network fetch is monkeypatched where its fallback is tested.
"""
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services.currency import SUPPORTED_CURRENCIES  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "refresh_exchange_rates", project_root / "scripts" / "refresh_exchange_rates.py"
)
rx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rx)

FRANKFURTER_USD = {
    "amount": 1.0, "base": "USD", "date": "2026-10-23",
    "rates": {"EUR": 0.9, "GBP": 0.77, "INR": 83.0, "JPY": 150.0, "CAD": 1.37, "AUD": 1.5,
              "BRL": 5.5, "CNY": 7.1, "SGD": 1.31, "CHF": 0.86, "KRW": 1370.0, "MXN": 19.0},
}

ECB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
    xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <gesmes:subject>Reference rates</gesmes:subject>
  <Cube>
    <Cube time="2026-10-23">
      <Cube currency="USD" rate="1.25"/>
      <Cube currency="JPY" rate="187.5"/>
      <Cube currency="GBP" rate="0.9625"/>
      <Cube currency="INR" rate="103.75"/>
      <Cube currency="CAD" rate="1.7125"/>
      <Cube currency="AUD" rate="1.875"/>
      <Cube currency="BRL" rate="6.875"/>
      <Cube currency="CNY" rate="8.875"/>
      <Cube currency="SGD" rate="1.6375"/>
      <Cube currency="CHF" rate="1.075"/>
      <Cube currency="KRW" rate="1712.5"/>
      <Cube currency="MXN" rate="23.75"/>
    </Cube>
  </Cube>
</gesmes:Envelope>
"""

TODAY = date(2026, 10, 26)


def _previous(path: Path, rates=None, as_of="2026-10-16", placeholder=True) -> Path:
    rates = rates or {"USD": 1.0, **{c: FRANKFURTER_USD["rates"][c] for c in FRANKFURTER_USD["rates"]}}
    path.write_text(json.dumps({"base": "USD", "as_of": as_of, "source": "authored placeholder",
                                "placeholder": placeholder, "rates": rates}, indent=2) + "\n", encoding="utf-8")
    return path


class TestParsing:
    def test_frankfurter_usd_base(self):
        as_of, rates = rx.parse_frankfurter(FRANKFURTER_USD)
        assert as_of == date(2026, 10, 23) and rates["USD"] == 1.0 and rates["EUR"] == 0.9

    def test_frankfurter_eur_base_is_rebased(self):
        payload = {"base": "EUR", "date": "2026-10-23", "rates": {"USD": 1.25, "GBP": 0.9625}}
        _, rates = rx.parse_frankfurter(payload)
        assert rates["USD"] == 1.0 and rates["EUR"] == pytest.approx(0.8) and rates["GBP"] == pytest.approx(0.77)

    def test_ecb_xml_rebased_to_usd(self):
        as_of, rates = rx.parse_ecb_xml(ECB_XML)
        assert as_of == date(2026, 10, 23)
        assert rates["USD"] == 1.0
        assert rates["EUR"] == pytest.approx(1 / 1.25)
        assert rates["JPY"] == pytest.approx(150.0) and rates["GBP"] == pytest.approx(0.77)
        assert rates["KRW"] == pytest.approx(1370.0)

    def test_rebase_without_usd_fails(self):
        with pytest.raises(rx.RefreshError):
            rx.rebase_eur_to_usd({"GBP": 0.9})

    @pytest.mark.parametrize("payload", [{"base": "USD", "date": "nope", "rates": {"EUR": 1}},
                                         {"base": "USD", "date": "2026-10-23"},
                                         {"base": "CHF", "date": "2026-10-23", "rates": {"EUR": 1}}, []])
    def test_bad_frankfurter_payloads_raise(self, payload):
        with pytest.raises(rx.RefreshError):
            rx.parse_frankfurter(payload)

    def test_bad_xml_raises(self):
        with pytest.raises(rx.RefreshError):
            rx.parse_ecb_xml("<not xml")
        with pytest.raises(rx.RefreshError):
            rx.parse_ecb_xml("<Envelope><Cube/></Envelope>")

    def test_detect_and_parse_both_kinds(self):
        assert rx.detect_and_parse(json.dumps(FRANKFURTER_USD))[2] == "frankfurter"
        assert rx.detect_and_parse(ECB_XML)[2] == "ecb"
        with pytest.raises(rx.RefreshError):
            rx.detect_and_parse("garbage")


class TestValidation:
    def _good(self):
        _, rates = rx.parse_frankfurter(FRANKFURTER_USD)
        return rates

    def test_accepts_a_good_table(self):
        out = rx.validate(date(2026, 10, 23), self._good(), None, today=TODAY)
        assert set(out) == set(SUPPORTED_CURRENCIES) and out["USD"] == 1.0

    def test_rejects_missing_code(self):
        rates = self._good()
        del rates["MXN"]
        with pytest.raises(rx.RefreshError, match="missing rate for MXN"):
            rx.validate(date(2026, 10, 23), rates, None, today=TODAY)

    def test_rejects_future_date(self):
        with pytest.raises(rx.RefreshError, match="in the future"):
            rx.validate(date(2026, 10, 27), self._good(), None, today=TODAY)

    @pytest.mark.parametrize("bad", [0.0, -1.0, float("inf"), float("nan"), "x"])
    def test_rejects_non_positive_or_non_finite(self, bad):
        rates = self._good()
        rates["EUR"] = bad
        with pytest.raises(rx.RefreshError):
            rx.validate(date(2026, 10, 23), rates, None, today=TODAY)

    def test_rejects_usd_not_one(self):
        rates = self._good()
        rates["USD"] = 1.01
        with pytest.raises(rx.RefreshError, match="USD must be exactly 1.0"):
            rx.validate(date(2026, 10, 23), rates, None, today=TODAY)

    def test_rejects_large_move_unless_forced(self, tmp_path):
        previous = json.loads(_previous(tmp_path / "p.json", placeholder=False).read_text())
        rates = self._good()
        rates["INR"] = 83.0 * 1.2  # +20%
        with pytest.raises(rx.RefreshError, match="INR moved 20.0%"):
            rx.validate(date(2026, 10, 23), rates, previous, today=TODAY)
        assert rx.validate(date(2026, 10, 23), rates, previous, today=TODAY, force=True)["INR"] == pytest.approx(99.6)
        # exactly at the limit passes
        rates["INR"] = 83.0 * 1.15
        rx.validate(date(2026, 10, 23), rates, previous, today=TODAY)

    def test_placeholder_previous_does_not_gate_moves(self, tmp_path):
        """The authored placeholder was never a market rate: if a real rate is >15% away from
        the hand-guessed one, the first refresh must still go through (otherwise the weekly job
        would skip forever and customers would keep seeing rate_placeholder: true)."""
        previous = json.loads(_previous(tmp_path / "p.json", placeholder=True).read_text())
        rates = self._good()
        rates["INR"] = 83.0 * 1.5  # +50% vs the placeholder guess
        assert rx.validate(date(2026, 10, 23), rates, previous, today=TODAY)["INR"] == pytest.approx(124.5)
        # every other check still applies against a placeholder previous
        del rates["GBP"]
        with pytest.raises(rx.RefreshError, match="missing rate for GBP"):
            rx.validate(date(2026, 10, 23), rates, previous, today=TODAY)

    def test_every_problem_is_reported_at_once(self):
        rates = self._good()
        del rates["MXN"]
        rates["EUR"] = -1
        with pytest.raises(rx.RefreshError) as exc:
            rx.validate(date(2026, 10, 27), rates, None, today=TODAY)
        msg = str(exc.value)
        assert "MXN" in msg and "EUR" in msg and "future" in msg


class TestCli:
    def test_report_only_does_not_write(self, tmp_path, capsys):
        out = _previous(tmp_path / "rates.json")
        before = out.read_bytes()
        payload = tmp_path / "payload.json"
        payload.write_text(json.dumps(FRANKFURTER_USD))
        assert rx.main(["--from-file", str(payload), "--out", str(out), "--date", "2026-10-26"]) == 0
        assert out.read_bytes() == before
        assert "report only" in capsys.readouterr().out

    def test_apply_writes_and_clears_placeholder(self, tmp_path, capsys):
        out = _previous(tmp_path / "rates.json")
        payload = tmp_path / "payload.json"
        payload.write_text(json.dumps(FRANKFURTER_USD))
        assert rx.main(["--from-file", str(payload), "--out", str(out), "--apply", "--date", "2026-10-26"]) == 0
        written = json.loads(out.read_text())
        assert written["placeholder"] is False and written["base"] == "USD" and written["as_of"] == "2026-10-23"
        assert set(written["rates"]) == set(SUPPORTED_CURRENCIES) and written["rates"]["USD"] == 1.0
        assert "Frankfurter" in written["source"]
        assert "| EUR |" in capsys.readouterr().out

    def test_apply_from_ecb_xml(self, tmp_path):
        out = _previous(tmp_path / "rates.json")
        payload = tmp_path / "ecb.xml"
        payload.write_text(ECB_XML)
        assert rx.main(["--from-file", str(payload), "--out", str(out), "--apply", "--date", "2026-10-26"]) == 0
        written = json.loads(out.read_text())
        assert written["rates"]["EUR"] == pytest.approx(0.8) and "ECB" in written["source"]

    def test_failure_leaves_file_byte_identical_and_exits_zero(self, tmp_path, capsys):
        out = _previous(tmp_path / "rates.json")
        before = out.read_bytes()
        bad = dict(FRANKFURTER_USD, rates={k: v for k, v in FRANKFURTER_USD["rates"].items() if k != "GBP"})
        payload = tmp_path / "payload.json"
        payload.write_text(json.dumps(bad))
        assert rx.main(["--from-file", str(payload), "--out", str(out), "--apply", "--date", "2026-10-26"]) == 0
        assert out.read_bytes() == before
        text = capsys.readouterr().out
        assert "refresh skipped" in text and "GBP" in text and "left untouched" in text

    def test_big_move_blocked_then_forced(self, tmp_path):
        out = _previous(tmp_path / "rates.json", placeholder=False)
        before = out.read_bytes()
        moved = dict(FRANKFURTER_USD, rates=dict(FRANKFURTER_USD["rates"], JPY=150.0 * 1.3))
        payload = tmp_path / "payload.json"
        payload.write_text(json.dumps(moved))
        base = ["--from-file", str(payload), "--out", str(out), "--apply", "--date", "2026-10-26"]
        assert rx.main(base) == 0 and out.read_bytes() == before
        assert rx.main(base + ["--force"]) == 0 and out.read_bytes() != before

    def test_missing_payload_file_exits_zero(self, tmp_path):
        out = _previous(tmp_path / "rates.json")
        assert rx.main(["--from-file", str(tmp_path / "nope.json"), "--out", str(out), "--apply"]) == 0

    def test_fetch_falls_back_to_ecb(self, monkeypatch):
        calls = []

        def fake_fetch(source, timeout=30.0):
            calls.append(source)
            if source == "frankfurter":
                raise ConnectionError("offline")
            return rx.parse_ecb_xml(ECB_XML) + ("ecb",)

        monkeypatch.setattr(rx, "fetch", fake_fetch)
        as_of, rates, source = rx.fetch_with_fallback("frankfurter")
        assert calls == ["frankfurter", "ecb"] and source == "ecb" and rates["USD"] == 1.0

    def test_every_source_failing_is_a_refresh_error(self, monkeypatch):
        monkeypatch.setattr(rx, "fetch", lambda source, timeout=30.0: (_ for _ in ()).throw(ConnectionError("x")))
        with pytest.raises(rx.RefreshError, match="every source failed"):
            rx.fetch_with_fallback("ecb")

    def test_vendored_table_passes_its_own_validation(self):
        raw = json.loads((project_root / "data" / "exchange_rates.json").read_text())
        rx.validate(date.fromisoformat(raw["as_of"]), raw["rates"], None)


class TestWorkflowJob:
    text = (project_root / ".github" / "workflows" / "price-audit.yml").read_text(encoding="utf-8")

    def test_job_exists_after_snapshot_and_before_reconfirm(self):
        assert "refresh-exchange-rates:" in self.text
        keys = ("refresh-snapshot:", "refresh-exchange-rates:", "reconfirm:")
        snapshot, ours, reconfirm = (self.text.index(k) for k in keys)
        assert snapshot < ours < reconfirm

    def test_job_runs_script_and_currency_tests_before_opening_a_pr(self):
        job = self.text.split("refresh-exchange-rates:", 1)[1].split("reconfirm:", 1)[0]
        assert "python scripts/refresh_exchange_rates.py --apply" in job
        assert "pytest tests/test_currency.py" in job
        assert "gh pr create" in job
        keys = ("refresh_exchange_rates.py --apply", "tests/test_currency.py", "gh pr create")
        script, tests, pr = (job.index(k) for k in keys)
        assert script < tests < pr
        assert "git diff --quiet -- data/exchange_rates.json" in job
        assert "ci/refresh-exchange-rates-" in job
        assert "--base develop" in job
        assert "Verified before this PR was opened" in job

    def test_job_does_not_depend_on_the_snapshot_refresh(self):
        # v1.77.1: refresh-snapshot had rejected three refreshes in a row, and this job
        # sat behind it with `needs: refresh-snapshot`, so the placeholder rates were
        # never replaced. It now only needs the audit job.
        job = self.text.split("refresh-exchange-rates:", 1)[1].split("reconfirm:", 1)[0]
        assert "needs: audit" in job
        assert "needs: refresh-snapshot" not in job

    def test_pickup_step_isolation_still_holds(self):
        # tests/test_registry_pickup.py splits on "pickup:"; our job must not contain it
        job = self.text.split("refresh-exchange-rates:", 1)[1].split("reconfirm:", 1)[0]
        assert "pickup:" not in job

    def test_dockerfile_ships_the_table(self):
        docker = (project_root / "Dockerfile").read_text(encoding="utf-8")
        assert "COPY data/ ./data/" in docker
        assert "COPY data/exchange_rates.json ./vendored/exchange_rates.json" in docker
        fly = (project_root / "fly.toml").read_text(encoding="utf-8")
        assert 'EXCHANGE_RATES_PATH = "/app/vendored/exchange_rates.json"' in fly
