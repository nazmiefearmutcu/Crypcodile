import pytest
from crypcodile.exchanges.base_onchain.trace_analyzer import TraceAnalyzer

def test_trace_analyzer_reentrancy_parity():
    analyzer = TraceAnalyzer()
    
    trace_result = {
        "result": [
            {
                "type": "call",
                "action": {
                    "from": "0xattacker",
                    "to": "0xvictim",
                    "callType": "call",
                    "input": "0x1234"
                },
                "traceAddress": []
            },
            {
                "type": "call",
                "action": {
                    "from": "0xvictim",
                    "to": "0xattacker",
                    "callType": "call",
                    "input": "0x5678"
                },
                "traceAddress": [0]
            },
            {
                "type": "call",
                "action": {
                    "from": "0xattacker",
                    "to": "0xvictim",
                    "callType": "call",
                    "input": "0x90ab"
                },
                "traceAddress": [0, 0]
            }
        ]
    }
    
    res = analyzer.analyze_trace("0xtx", trace_result)
    assert res["reentrancy_detected"] is True
    assert res["flashloans"] is False

def test_trace_analyzer_flashloan_parity():
    analyzer = TraceAnalyzer()
    
    trace_result = {
        "result": [
            {
                "type": "call",
                "action": {
                    "from": "0xuser",
                    "to": "0xpool",
                    "callType": "call",
                    "input": "0x4907765600000000000000"
                },
                "traceAddress": []
            }
        ]
    }
    res = analyzer.analyze_trace("0xtx", trace_result)
    assert res["flashloans"] is True
    assert len(res["internal_swaps"]) == 0

def test_trace_analyzer_internal_swap_parity():
    analyzer = TraceAnalyzer()
    
    trace_result = {
        "result": [
            {
                "type": "call",
                "action": {
                    "from": "0xrouter",
                    "to": "0xpool",
                    "callType": "call",
                    "input": "0x128acb080000000000000"
                },
                "traceAddress": [0]
            }
        ]
    }
    res = analyzer.analyze_trace("0xtx", trace_result)
    assert len(res["internal_swaps"]) == 1
    assert res["internal_swaps"][0]["pool"] == "0xpool"
    assert res["internal_swaps"][0]["protocol"] == "uniswap_v3"

def test_trace_analyzer_reentrancy_geth():
    analyzer = TraceAnalyzer()
    
    trace_result = {
        "structLogs": [
            {
                "depth": 1,
                "op": "CALL",
                "stack": [
                    "0x0",
                    "0x000000000000000000000000victim"
                ]
            },
            {
                "depth": 2,
                "op": "CALL",
                "stack": [
                    "0x0",
                    "0x000000000000000000000000victim"
                ]
            }
        ]
    }
    res = analyzer.analyze_trace("0xtx", trace_result)
    assert res["reentrancy_detected"] is True
