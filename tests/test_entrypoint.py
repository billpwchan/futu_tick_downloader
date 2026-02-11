import sys
import types

if "futu" not in sys.modules:
    fake_futu = types.ModuleType("futu")
    fake_futu.RET_OK = 0
    fake_futu.Session = types.SimpleNamespace(ALL="ALL")
    fake_futu.SubType = types.SimpleNamespace(TICKER="TICKER")
    fake_futu.OpenQuoteContext = object

    class _TickerHandlerBase:
        def on_recv_rsp(self, rsp_pb):
            return 0, rsp_pb

    fake_futu.TickerHandlerBase = _TickerHandlerBase
    sys.modules["futu"] = fake_futu

import hk_tick_collector.__main__ as app_entry


def test_module_entrypoint_invokes_run_main(monkeypatch):
    called = {"value": False}

    def _fake_run_main():
        called["value"] = True

    monkeypatch.setattr(app_entry, "run_main", _fake_run_main)
    app_entry.entrypoint()

    assert called["value"] is True
