"""估值页的产业链映射。

每只股票只归一个主环节，写成代码到（大类、环节、是否分销）的显式数据。
未出现在映射里的已发布股票归入「其他已估值」，不按数据源行业名猜测。
"""

from __future__ import annotations

CHAIN_AI = "AI 上下游"
CHAIN_ROBOT = "机器人上下游"
CHAIN_OTHER = "其他已估值"
CHAIN_ORDER = (CHAIN_AI, CHAIN_ROBOT, CHAIN_OTHER)

SEGMENT_COMPUTE = "算力芯片"
SEGMENT_EQUIPMENT = "半导体设备与材料"
SEGMENT_OPTICAL = "光模块与通信"
SEGMENT_SERVER = "服务器、PCB 与散热"
SEGMENT_SOFTWARE = "软件与应用"

SEGMENT_REDUCER = "减速器"
SEGMENT_SERVO = "伺服与执行器"
SEGMENT_SENSOR = "传感与视觉"
SEGMENT_TRANSMISSION = "传动件"
SEGMENT_BODY = "本体"
SEGMENT_LINE = "产线与物流应用"

AI_SEGMENT_ORDER = (
    SEGMENT_COMPUTE,
    SEGMENT_EQUIPMENT,
    SEGMENT_OPTICAL,
    SEGMENT_SERVER,
    SEGMENT_SOFTWARE,
)
ROBOT_SEGMENT_ORDER = (
    SEGMENT_REDUCER,
    SEGMENT_SERVO,
    SEGMENT_SENSOR,
    SEGMENT_TRANSMISSION,
    SEGMENT_BODY,
    SEGMENT_LINE,
)
SEGMENT_ORDER = {
    CHAIN_AI: AI_SEGMENT_ORDER,
    CHAIN_ROBOT: ROBOT_SEGMENT_ORDER,
}

OTHER_SEGMENT_LABEL = "—"

# 主板 AI 清单（2026-11-16，57 只）按主业归入五个环节。
# 雪人集团 002639.SZ 不在这份映射里，页面上归入「其他已估值」。
# 科创板、创业板里已经发布、且主业就是这条链的：寒武纪、长鑫科技、中芯国际、中微公司、源杰科技、中际旭创。
_AI_GROUPS: tuple[tuple[str, tuple[str, ...], frozenset[str]], ...] = (
    (
        SEGMENT_COMPUTE,
        (
            "002156.SZ",  # 通富微电，先进封装
            "002185.SZ",  # 华天科技，封测
            "600584.SH",  # 长电科技，封测
            "603986.SH",  # 兆易创新，存储与 MCU
            "688256.SH",  # 寒武纪，AI 芯片设计
            "688825.SH",  # 长鑫科技，存储制造
            "688981.SH",  # 中芯国际，晶圆制造
        ),
        frozenset(),
    ),
    (
        SEGMENT_EQUIPMENT,
        (
            "002371.SZ",  # 北方华创，刻蚀与薄膜设备
            "002409.SZ",  # 雅克科技，前驱体与电子材料
            "002436.SZ",  # 兴森科技，IC 封装基板
            "600641.SH",  # 先导基电，离子注入设备
            "603061.SH",  # 金海通，测试分选设备
            "603078.SH",  # 江化微，湿电子化学品
            "603650.SH",  # 彤程新材，光刻胶材料
            "603690.SH",  # 至纯科技，晶圆厂工艺系统
            "605358.SH",  # 立昂微，半导体硅片
            "688012.SH",  # 中微公司，刻蚀设备
        ),
        frozenset(),
    ),
    (
        SEGMENT_OPTICAL,
        (
            "000063.SZ",  # 中兴通讯，交换机与光传输
            "000938.SZ",  # 紫光股份，交换机与企业网络
            "000988.SZ",  # 华工科技，光模块
            "002281.SZ",  # 光迅科技，光模块
            "002396.SZ",  # 星网锐捷，企业网络设备
            "002475.SZ",  # 立讯精密，高速连接器
            "003031.SZ",  # 中瓷电子，光通信陶瓷外壳
            "300308.SZ",  # 中际旭创，光模块
            "600487.SH",  # 亨通光电，光纤光缆
            "600498.SH",  # 烽火通信，光传输设备
            "600522.SH",  # 中天科技，光纤光缆
            "601869.SH",  # 长飞光纤，光纤光缆
            "603083.SH",  # 剑桥科技，光模块与交换机
            "603118.SH",  # 共进股份，交换机代工
            "688498.SH",  # 源杰科技，光芯片
        ),
        frozenset(),
    ),
    (
        SEGMENT_SERVER,
        (
            "000628.SZ",  # 高新发展，昇腾服务器
            "000977.SZ",  # 浪潮信息，服务器
            "001389.SZ",  # 广合科技，服务器 PCB
            "002261.SZ",  # 拓维信息，昇腾服务器
            "002335.SZ",  # 科华数据，不间断电源
            "002364.SZ",  # 中恒电气，数据中心电源
            "002384.SZ",  # 东山精密，印制电路板
            "002463.SZ",  # 沪电股份，服务器 PCB
            "002518.SZ",  # 科士达，不间断电源
            "002837.SZ",  # 英维克，液冷
            "002851.SZ",  # 麦格米特，电源
            "002916.SZ",  # 深南电路，服务器电路板
            "600183.SH",  # 生益科技，覆铜板
            "600602.SH",  # 云赛智联，数据中心
            "600845.SH",  # 宝信软件，批发型数据中心
            "601138.SH",  # 工业富联，服务器代工
            "603002.SH",  # 宏昌电子，环氧树脂与覆铜板
            "603019.SH",  # 中科曙光，服务器
            "603186.SH",  # 华正新材，覆铜板
            "603256.SH",  # 宏和科技，电子玻纤布
            "603296.SH",  # 华勤技术，服务器代工
            "603881.SH",  # 数据港，批发型数据中心
            "603887.SH",  # 城地香江，数据中心
            "603912.SH",  # 佳力图，机房空调
        ),
        frozenset(),
    ),
    (
        SEGMENT_SOFTWARE,
        (
            "000034.SZ",  # 神州数码，AI 服务器分销
            "002230.SZ",  # 科大讯飞，行业 AI 应用
            "002236.SZ",  # 大华股份，安防
            "002415.SZ",  # 海康威视，安防
            "002920.SZ",  # 德赛西威，智能座舱
            "002970.SZ",  # 锐明技术，车载 AI
            "600570.SH",  # 恒生电子，金融 IT
        ),
        frozenset({"000034.SZ"}),
    ),
)

# 同时像 AI 和机器人的，只放这里。宇树科技只归本体。
_ROBOT_GROUPS: tuple[tuple[str, tuple[str, ...], frozenset[str]], ...] = (
    (
        SEGMENT_REDUCER,
        (
            "000837.SZ",  # 秦川机床
            "001306.SZ",  # 夏厦精密
            "002031.SZ",  # 巨轮智能
            "002472.SZ",  # 双环传动
            "002896.SZ",  # 中大力德
            "300503.SZ",  # 昊志机电
            "301368.SZ",  # 丰立智能
            "301550.SZ",  # 斯菱智驱
            "688017.SH",  # 绿的谐波
            "920510.BJ",  # 丰光精密
        ),
        frozenset(),
    ),
    (
        SEGMENT_SERVO,
        (
            "002050.SZ",  # 三花智控
            "002979.SZ",  # 雷赛智能
            "300124.SZ",  # 汇川技术
            "301510.SZ",  # 固高科技
            "601689.SH",  # 拓普集团
            "603728.SH",  # 鸣志电器
            "688160.SH",  # 步科股份
            "688320.SH",  # 禾川科技
        ),
        frozenset(),
    ),
    (
        SEGMENT_SENSOR,
        (
            "002338.SZ",  # 奥普光电
            "603662.SH",  # 柯力传感
            "688322.SH",  # 奥比中光
            "688400.SH",  # 凌云光
            "688686.SH",  # 奥普特
        ),
        frozenset(),
    ),
    (
        SEGMENT_TRANSMISSION,
        (
            "002553.SZ",  # 南方精工
            "300580.SZ",  # 贝斯特
            "300718.SZ",  # 长盛轴承
            "600592.SH",  # 龙溪股份
            "603667.SH",  # 五洲新春
            "920593.BJ",  # 鼎智科技
        ),
        frozenset(),
    ),
    (
        SEGMENT_BODY,
        (
            "002527.SZ",  # 新时达
            "002747.SZ",  # 埃斯顿
            "300024.SZ",  # 机器人
            "300607.SZ",  # 拓斯达
            "688165.SH",  # 埃夫特
            "688255.SH",  # 凯尔达
            "688836.SH",  # 宇树科技
        ),
        frozenset(),
    ),
    (
        SEGMENT_LINE,
        (
            "002698.SZ",  # 博实股份
            "002957.SZ",  # 科瑞技术
            "300532.SZ",  # 今天国际
            "301311.SZ",  # 昆船智能
            "603283.SH",  # 赛腾股份
            "603611.SH",  # 诺力股份
            "603895.SH",  # 天永智能
            "603960.SH",  # 克来机电
            "688090.SH",  # 瑞松科技
            "688097.SH",  # 博众精工
            "688155.SH",  # 先惠技术
            "688162.SH",  # 巨一科技
            "688211.SH",  # 中科微至
            "688218.SH",  # 江苏北人
            "688251.SH",  # 井松智能
            "688306.SH",  # 均普智能
            "688360.SH",  # 德马科技
            "688529.SH",  # 豪森智能
            "688557.SH",  # 兰剑智能
            "688648.SH",  # 中邮科技
        ),
        frozenset(),
    ),
)


def _compile(chain: str, groups: tuple[tuple[str, tuple[str, ...], frozenset[str]], ...]) -> dict[str, tuple[str, str, bool]]:
    compiled: dict[str, tuple[str, str, bool]] = {}
    for segment, codes, distributors in groups:
        if not distributors <= set(codes):
            missing = distributors - set(codes)
            raise ValueError(f"{segment} 的分销商标记不在该环节里：{sorted(missing)}")
        for code in codes:
            if code in compiled:
                raise ValueError(f"重复映射 {code}")
            compiled[code] = (chain, segment, code in distributors)
    return compiled


MAPPING: dict[str, tuple[str, str, bool]] = {}
for _chain, _groups in ((CHAIN_AI, _AI_GROUPS), (CHAIN_ROBOT, _ROBOT_GROUPS)):
    _part = _compile(_chain, _groups)
    _overlap = set(MAPPING) & set(_part)
    if _overlap:
        raise ValueError(f"同一只股票归了两个大类：{sorted(_overlap)}")
    MAPPING.update(_part)


def placement(code: str) -> tuple[str, str | None, bool]:
    """返回大类、环节、是否分销。没有映射的大类是「其他已估值」，环节为空。"""
    found = MAPPING.get(str(code or "").strip())
    if found is None:
        return CHAIN_OTHER, None, False
    return found


def segment_label(code: str) -> str:
    """排名表里的环节。分销商写成「环节·分销」，其他已估值写成「—」。"""
    chain, segment, distributor = placement(code)
    if chain == CHAIN_OTHER or not segment:
        return OTHER_SEGMENT_LABEL
    if distributor:
        return f"{segment}·分销"
    return segment


def matches(code: str, chain: str, segment: str | None = None) -> bool:
    """是否落在这个大类。给了环节时再收成该环节，分销商仍留在原环节里。"""
    placed_chain, placed_segment, _ = placement(code)
    if placed_chain != chain:
        return False
    if segment:
        return placed_segment == segment
    return True


def available_chains(codes) -> list[str]:
    """有已发布股票的大类，按固定顺序。"""
    found = {placement(code)[0] for code in codes or []}
    return [name for name in CHAIN_ORDER if name in found]


def available_segments(codes, chain: str) -> list[str]:
    """这个大类里有已发布股票的环节。「其他已估值」没有第二排。"""
    order = SEGMENT_ORDER.get(chain)
    if not order:
        return []
    found: set[str] = set()
    for code in codes or []:
        placed_chain, segment, _ = placement(code)
        if placed_chain == chain and segment:
            found.add(segment)
    return [name for name in order if name in found]
