"""Regressões do protocolo de report do scanner VPS.

Roda sem banco nem rede:
    python tests/test_vps_scanner_reports.py
"""
from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path


_SCANNER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "vps_scanner.py"
_SPEC = importlib.util.spec_from_file_location("vps_scanner_test", _SCANNER_PATH)
assert _SPEC and _SPEC.loader
scanner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(scanner)


def _report_base() -> dict:
    return {
        "worker_id": "scanner-test",
        "task_id": 123,
        "lease_token": "a" * 32,
        "found_count": 1,
        "error_count": 0,
    }


def test_deep_process_grande_vira_reports_limitados_e_reconstituiveis():
    events = [{"EventId": str(i), "payload": "x" * 700_000} for i in range(8)]
    payload = [{
        "_deep_process": True,
        "battle_id": 42,
        "raw": {"id": 42, "players": {"a": {"name": "teste"}}},
        "events": events,
    }]

    reports = scanner._build_report_bodies(_report_base(), payload)

    assert len(reports) > 1
    assert all("payload_chunk" in report for report in reports)
    assert all(
        len(json.dumps(report, separators=(",", ":")).encode())
        <= scanner.REPORT_BODY_MAX_BYTES
        for report in reports
    )
    rebuilt = b"".join(base64.b64decode(report["payload_chunk"], validate=True) for report in reports)
    assert json.loads(rebuilt) == payload
    assert [report["chunk_index"] for report in reports] == list(range(len(reports)))
    assert {report["chunk_count"] for report in reports} == {len(reports)}


def test_report_pequeno_mantem_formato_simples():
    payload = [{"id": "battle-1"}]

    reports = scanner._build_report_bodies(_report_base(), payload)

    assert reports == [{**_report_base(), "data": payload}]


def test_report_maior_que_o_limite_total_falha_antes_do_upload():
    payload = [{"_deep_process": True, "raw": "x" * (scanner.REPORT_PAYLOAD_MAX_BYTES + 1)}]

    try:
        scanner._build_report_bodies(_report_base(), payload)
    except ValueError as exc:
        assert "limite total" in str(exc)
    else:
        raise AssertionError("payload acima do limite total foi aceito")


def test_erro_413_refatia_o_spool_sem_nova_coleta():
    data = [{"_deep_process": True, "raw": "x" * 1_000_000}]
    reports = scanner._build_report_bodies(_report_base(), data)

    reduced = scanner._rechunk_report_spool({"reports": reports}, chunk_bytes=128 * 1024)

    assert reduced is not None
    assert len(reduced["reports"]) > len(reports)
    rebuilt = b"".join(
        base64.b64decode(report["payload_chunk"])
        for report in reduced["reports"]
    )
    assert json.loads(rebuilt) == data


def test_report_pendente_renova_lease_e_reenvia_sem_nova_coleta():
    calls = []
    original_request = scanner._request

    def request(method, path, body=None, timeout=30):
        calls.append((method, path, body, timeout))
        return (200, {"accepted": 1, "rejected": 0})

    setattr(scanner, "_request", request)
    try:
        report = {**_report_base(), "data": [{"id": "battle-1"}]}
        sent, status = scanner._send_report_spool({"reports": [report]})
    finally:
        setattr(scanner, "_request", original_request)

    assert (sent, status) == (True, 202)
    assert [call[1] for call in calls] == ["/scan/renew", "/scan/report"]
    assert calls[1][2] == report


if __name__ == "__main__":
    test_deep_process_grande_vira_reports_limitados_e_reconstituiveis()
    test_report_pequeno_mantem_formato_simples()
    test_report_maior_que_o_limite_total_falha_antes_do_upload()
    test_erro_413_refatia_o_spool_sem_nova_coleta()
    test_report_pendente_renova_lease_e_reenvia_sem_nova_coleta()
    print("vps scanner reports OK")
