# -*- coding: utf-8 -*-
"""trigger/effect 邏輯塊 -> 人類可讀中文文字的規則模板翻譯器。

設計：規則表 List[(matcher, formatter)]，按登記順序(即特異性由高到低)嘗試比對，第一個命中的生效。
兜底規則(_fallback)必定命中，保證每個 key/value 都有輸出，絕不靜默丟棄——翻不了的原樣顯示
`key = value`並標註「未能自動翻譯」，引導使用者/agent 去查原始腳本附錄核實，避免產生幻覺性數值。

輸入應為已用 macro_expand.expand_block() 展開過巨集的 Block（否則巨集呼叫節點會落到兜底規則，
只顯示巨集名，翻不出實際條件）。
"""
import re
from .parser import Block
from .localisation import clean_text

_PCT_SUFFIXES = ("_modifier", "_power", "_cost", "_chance", "_speed", "_efficiency")
_ADD_POWER_MAP = {
    "add_adm_power": "行政国力", "add_dip_power": "外交国力", "add_mil_power": "军事国力",
    "add_prestige": "威望", "add_legitimacy": "统治正统性", "add_stability": "稳定度",
    "add_manpower": "人力", "add_treasury": "国库金钱", "add_army_tradition": "陆军传统",
    "add_navy_tradition": "海军传统", "add_devotion": "虔诚度", "add_horde_unity": "部落凝聚力",
    "add_meritocracy": "唯才是举度", "add_absolutism": "专制主义", "add_karma": "业力",
    "add_republican_tradition": "共和传统", "add_papal_influence": "教廷影响力",
    "add_yearly_manpower": "年人力", "add_country_modifier": "国家修正",
}
_SCOPE_LABELS = {"root": "我方", "from": "触发国", "prev": "前一层作用域", "this": "本作用域"}


def _is_pct_key(key):
    kl = key.lower()
    return any(kl.endswith(suf) for suf in _PCT_SUFFIXES)

def _fmt_number(raw):
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None, raw
    if abs(v - round(v)) < 1e-9:
        return v, str(int(round(v)))
    return v, f"{v:g}"

def _fmt_pct(raw):
    v, _ = _fmt_number(raw)
    if v is None:
        return None
    pct = v * 100
    s = str(int(round(pct))) if abs(pct - round(pct)) < 1e-6 else f"{pct:g}"
    sign = "+" if pct >= 0 else ""
    return f"{sign}{s}%"

def _scope_label(v):
    return _SCOPE_LABELS.get((v or "").lower(), v)

def _loc_or_titlecase(key, loc):
    if loc is not None:
        return loc.get(key)
    return re.sub(r'[_\-]+', ' ', key).strip().title()


# ---------------- 個別 formatter ----------------

def _fmt_logic(key, value, loc, tr):
    kl = key.lower()
    if not isinstance(value, Block):
        return [f"`{key} = {value}`（邏輯運算子缺少子條件，見附錄）"]
    sub = tr(value, loc)
    if not sub:
        sub = ["（無子條件）"]
    if kl == "and":
        head = "全部满足："
    elif kl == "or":
        head = "满足以下任一："
    else:  # not
        head = "不满足："
    return [head] + ["  " + s for s in sub]

def _fmt_has_dlc(key, value, loc, tr):
    return [f"需要 DLC「{value}」"]

def _fmt_is_core(key, value, loc, tr):
    return [f"是{_scope_label(value)}的核心省份"]

def _fmt_is_claim(key, value, loc, tr):
    return [f"是{_scope_label(value)}的宣称地"]

def _fmt_tag(key, value, loc, tr):
    return [f"国家为 {value}"]

def _fmt_add_power(key, value, loc, tr):
    kl = key.lower()
    label = _ADD_POWER_MAP.get(kl)
    _, num_s = _fmt_number(value)
    if label:
        return [f"获得 {num_s} 点{label}" if not str(value).startswith("-") else f"损失 {num_s.lstrip('-')} 点{label}"]
    return [f"{_loc_or_titlecase(key, loc)}：{num_s}"]

def _fmt_pct_modifier(key, value, loc, tr):
    pct = _fmt_pct(value)
    label = _loc_or_titlecase(key, loc)
    if pct is None:
        return [f"{label}：{value}"]
    return [f"{label} {pct}"]

def _fmt_gov_attribute(key, value, loc, tr):
    return [f"政体具有「{_loc_or_titlecase(value, loc)}」属性"]

def _fmt_flag(key, value, loc, tr):
    return [f"已触发内部标记「{value}」（游戏内部状态，非直接可见数值，见附录原始脚本）"]

def _fmt_is_year(key, value, loc, tr):
    return [f"当前游戏年份为 {value} 年"]

def _fmt_has_reform(key, value, loc, tr):
    return [f"已进行「{_loc_or_titlecase(value, loc)}」政府改革"]

def _fmt_religion(key, value, loc, tr):
    return [f"信仰为「{_loc_or_titlecase(value, loc)}」"]

def _fmt_government(key, value, loc, tr):
    return [f"政体类型为「{_loc_or_titlecase(value, loc)}」"]

def _fmt_primary_culture(key, value, loc, tr):
    return [f"主要文化为「{_loc_or_titlecase(value, loc)}」"]

def _fmt_advisor(key, value, loc, tr):
    return [f"拥有顾问「{_loc_or_titlecase(value, loc)}」"]

def _fmt_estate_scope(key, value, loc, tr):
    return [f"针对阶层「{_loc_or_titlecase(value, loc)}」"]

def _fmt_share_num(key, value, loc, tr):
    _, num_s = _fmt_number(value)
    return [f"份额变化：{num_s}"]

def _fmt_icon(key, value, loc, tr):
    return []  # 純裝飾性圖示檔名，對閱讀理解無資訊量，直接略過(空列表=匹配成功但不輸出，不落到兜底規則)

def _fmt_group_scope(key, value, loc, tr):
    label = _loc_or_titlecase(key, loc)
    return [f"{label}为「{_loc_or_titlecase(value, loc)}」"]

def _fmt_bool_flag(key, value, loc, tr):
    label = _loc_or_titlecase(key, loc)
    yn = "是" if str(value).strip().lower() == "yes" else "否"
    return [f"{label}：{yn}"]

def _fmt_custom_tooltip(key, value, loc, tr):
    # custom_trigger_tooltip 包一層 tooltip(說明文字)+實際條件；把實際條件攤平即可，tooltip 本身略過
    if not isinstance(value, Block):
        return []
    inner_lines = []
    for k, v in value.items:
        if k is not None and k.lower() == "tooltip":
            continue
        inner_lines.extend(tr(Block([(k, v)]), loc))
    return inner_lines or ["（宏展开条件，详见附录）"]

def _fmt_limit(key, value, loc, tr):
    sub = tr(value, loc) if isinstance(value, Block) else []
    return ["前提条件："] + ["  " + s for s in sub] if sub else ["前提条件：（无）"]

def _fmt_number_generic(key, value, loc, tr):
    label = _loc_or_titlecase(key, loc)
    if _is_pct_key(key):
        pct = _fmt_pct(value)
        if pct is not None:
            return [f"{label} {pct}"]
    _, num_s = _fmt_number(value)
    if num_s is not None:
        return [f"{label}：{num_s}"]
    return None  # 非數值，交給下個規則

def _fmt_fallback(key, value, loc, tr):
    if isinstance(value, Block):
        sub = tr(value, loc)
        label = _loc_or_titlecase(key, loc) if loc is not None else key
        if sub:
            return [f"「{label}」（原始 key: {key}）："] + ["  " + s for s in sub]
        return [f"`{key} = {{}}`（空区块，未能自动翻译，见附录）"]
    return [f"`{key} = {value}`（未能自动翻译，见附录核实）"]


# ---------------- 規則表(matcher, formatter)：按特異性由高到低 ----------------
def _mk(keys):
    keys = set(k.lower() for k in keys)
    return lambda k, v: k.lower() in keys

RULES = [
    (_mk(["and", "or", "not"]), _fmt_logic),
    (_mk(["has_dlc"]), _fmt_has_dlc),
    (_mk(["is_core"]), _fmt_is_core),
    (_mk(["is_claim", "has_claim"]), _fmt_is_claim),
    (_mk(["tag", "original_tag", "overlord", "owner", "controller"]), _fmt_tag),
    (lambda k, v: k.lower() in _ADD_POWER_MAP, _fmt_add_power),
    (_mk(["has_government_attribute"]), _fmt_gov_attribute),
    (lambda k, v: k.lower().startswith("has_country_flag") or k.lower().startswith("has_ruler_flag")
                  or k.lower().startswith("has_global_flag"), _fmt_flag),
    (_mk(["is_year"]), _fmt_is_year),
    (_mk(["has_reform"]), _fmt_has_reform),
    (_mk(["religion", "add_harmonized_religion", "has_reform_religion"]), _fmt_religion),
    (_mk(["government"]), _fmt_government),
    (_mk(["primary_culture", "add_accepted_culture", "culture"]), _fmt_primary_culture),
    (_mk(["advisor"]), _fmt_advisor),
    (_mk(["estate"]), _fmt_estate_scope),
    (_mk(["share"]), _fmt_share_num),
    (_mk(["custom_trigger_tooltip", "custom_tooltip"]), _fmt_custom_tooltip),
    (_mk(["limit"]), _fmt_limit),
    (_mk(["icon"]), _fmt_icon),
    (lambda k, v: not isinstance(v, Block) and k.lower().endswith("_group"), _fmt_group_scope),
    (_mk(["region", "capital_scope", "colonial_region", "trade_company_region"]), _fmt_group_scope),
    (lambda k, v: not isinstance(v, Block) and _looks_numeric(v), _fmt_number_generic),
    (lambda k, v: not isinstance(v, Block) and str(v).strip().lower() in ("yes", "no"), _fmt_bool_flag),
    (lambda k, v: True, _fmt_fallback),   # 兜底，必定命中
]

def _looks_numeric(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def translate_block(block, loc=None):
    """把已展開巨集的 trigger/effect Block 翻譯成人類可讀的中文條列文字(List[str])。
    loc: LocalisationIndex 實例(可選)，用於把 modifier key 轉成友善名稱；不給則用 snake_case->Title Case。"""
    if not isinstance(block, Block):
        return []
    lines = []
    for k, v in block.items:
        if k is None:
            lines.append(f"- {v}")
            continue
        for matcher, formatter in RULES:
            if matcher(k, v):
                try:
                    out = formatter(k, v, loc, translate_block)
                except Exception as e:
                    out = [f"`{k} = {v}`（翻译规则执行异常: {e!r}，见附录核实）"]
                if out is None:
                    continue
                lines.extend(out)
                break
    return lines


def translate_block_md(block, loc=None):
    """回傳 Markdown bullet list 字串(供渲染器直接嵌入文件)。"""
    lines = translate_block(block, loc)
    if not lines:
        return "（无可翻译条件）"
    return "\n".join(f"- {l}" if not l.startswith("  ") else l for l in lines)
