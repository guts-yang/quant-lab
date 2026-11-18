"""估值页按产业链筛选，不按数据源行业导航。"""

import importlib.util
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def _load(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "web" / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _chains():
    return _load("quantlab_web_chains", "chains.py")


def _charts():
    return _load("quantlab_web_charts_chains", "charts.py")


def test_chain_mapping_is_unique_and_known_names_stay_put():
    chains = _chains()
    codes = list(chains.MAPPING)
    assert len(codes) == len(set(codes))
    assert chains.placement("002371.SZ") == (chains.CHAIN_AI, "半导体设备与材料", False)
    assert chains.placement("002837.SZ") == (chains.CHAIN_AI, "服务器、PCB 与散热", False)
    assert chains.placement("002415.SZ")[:2] == (chains.CHAIN_AI, "软件与应用")
    assert chains.placement("002236.SZ")[:2] == (chains.CHAIN_AI, "软件与应用")
    assert chains.placement("688836.SH") == (chains.CHAIN_ROBOT, "本体", False)
    assert chains.placement("002639.SZ") == (chains.CHAIN_OTHER, None, False)
    robot = {code for code, item in chains.MAPPING.items() if item[0] == chains.CHAIN_ROBOT}
    ai = {code for code, item in chains.MAPPING.items() if item[0] == chains.CHAIN_AI}
    assert robot.isdisjoint(ai)
    assert "688836.SH" not in ai


def test_empty_segments_stay_hidden_and_other_has_no_second_row():
    chains = _chains()
    codes = ["002371.SZ", "000034.SZ", "600519.SH", "688017.SH"]
    assert chains.available_segments(codes, chains.CHAIN_AI) == ["半导体设备与材料", "软件与应用"]
    assert "算力芯片" not in chains.available_segments(codes, chains.CHAIN_AI)
    assert chains.available_segments(codes, chains.CHAIN_ROBOT) == ["减速器"]
    assert "产线与物流应用" not in chains.available_segments(codes, chains.CHAIN_ROBOT)
    assert chains.available_segments(codes, chains.CHAIN_OTHER) == []
    assert chains.available_chains(codes) == [chains.CHAIN_AI, chains.CHAIN_ROBOT, chains.CHAIN_OTHER]
    assert chains.available_chains(["600519.SH"]) == [chains.CHAIN_OTHER]


def test_chain_ranking_restarts_at_one_and_keeps_source_industry():
    charts = _charts()
    chains = _chains()

    class _Run:
        def __init__(self, name, code, price, base, industry):
            self.code = code
            self.asof = "20261116"
            self.assumptions = {"name": name, "fundamentals": {"price": price, "industry": industry}}
            self.scenarios = pd.DataFrame({"scenario": ["bear", "base", "bull"], "per_share": [base - 1, base, base + 1]})

    runs = [
        _Run("北方华创", "002371.SZ", 10.0, 12.0, "半导体"),
        _Run("神州数码", "000034.SZ", 10.0, 15.0, "软件服务"),
        _Run("沪电股份", "002463.SZ", 10.0, 11.0, "元器件"),
        _Run("贵州茅台", "600519.SH", 10.0, 20.0, "白酒"),
        _Run("绿的谐波", "688017.SH", 10.0, 13.0, "机械基件"),
    ]
    ai = charts.chain_valuation_ranking(runs, chain=chains.CHAIN_AI)
    assert list(ai["公司"]) == ["神州数码", "北方华创", "沪电股份"]
    assert list(ai["排名"]) == [1, 2, 3]
    assert list(ai["环节"]) == ["软件与应用·分销", "半导体设备与材料", "服务器、PCB 与散热"]
    assert list(ai["行业"]) == ["软件服务", "半导体", "元器件"]
    assert "贵州茅台" not in set(ai["公司"])

    software = charts.chain_valuation_ranking(runs, chain=chains.CHAIN_AI, segment="软件与应用")
    assert list(software["公司"]) == ["神州数码"]
    assert list(software["排名"]) == [1]
    assert list(software["环节"]) == ["软件与应用·分销"]
    assert software["估值机会"].iloc[0] == 0.5

    other = charts.chain_valuation_ranking(runs, chain=chains.CHAIN_OTHER)
    assert list(other["公司"]) == ["贵州茅台"]
    assert list(other["环节"]) == ["—"]
    assert list(other["排名"]) == [1]
    assert list(other["行业"]) == ["白酒"]


def test_digital_china_is_marked_as_distributor_inside_software():
    chains = _chains()
    assert chains.placement("000034.SZ") == (chains.CHAIN_AI, "软件与应用", True)
    assert chains.segment_label("000034.SZ") == "软件与应用·分销"
    assert chains.matches("000034.SZ", chains.CHAIN_AI, "软件与应用")
    assert "软件与应用·分销" not in chains.available_segments(["000034.SZ"], chains.CHAIN_AI)
    assert chains.available_segments(["000034.SZ"], chains.CHAIN_AI) == ["软件与应用"]


def test_unmapped_code_goes_to_other_valued():
    chains = _chains()
    assert chains.placement("600900.SH") == (chains.CHAIN_OTHER, None, False)
    assert chains.placement("002594.SZ")[0] == chains.CHAIN_OTHER
    assert chains.placement("300750.SZ")[0] == chains.CHAIN_OTHER
    assert chains.placement("")[0] == chains.CHAIN_OTHER
    assert chains.segment_label("600036.SH") == "—"
    assert chains.matches("600519.SH", chains.CHAIN_OTHER)
    assert not chains.matches("600519.SH", chains.CHAIN_AI)
    assert not chains.matches("002639.SZ", chains.CHAIN_AI, "服务器、PCB 与散热")
