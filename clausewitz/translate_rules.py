# -*- coding: utf-8 -*-
"""trigger/effect 邏輯塊 -> 人類可讀「英文」文字的規則模板翻譯器。

英文是這個知識庫的來源真相(game_file 本體就是英文；名稱一律取遊戲自帶英文 localisation)。
每個命令取英文的三級策略：
  1) 遊戲自帶 loc 模板優先：白名單 _GAME_LOC_CMDS 裡的命令，用 loc.game_trigger() 取遊戲原句
     (如 HAS_REFORM -> "Has enacted Government Reform Mughal Diwan")——最權威，尽量用游戏原串。
  2) 手寫英文兜底：遊戲沒有乾淨 loc 模板的命令(邏輯運算子、has_dlc、旗標、修正增刪等)用簡潔英文，
     名稱仍來自遊戲英文 loc。這是對腳本邏輯的英文直白描述。
  3) 最終兜底(_fmt_fallback)必定命中：翻不了的原樣顯示 `key = value` 並標註「(unmapped — see raw
     script appendix)」，絕不靜默丟棄，引導去讀文末原始腳本附錄核實。

輸入應為已用 macro_expand.expand_block() 展開過巨集的 Block。

語言切換鉤子：這裡固定輸出英文(來源語言)。之後若要中文，做法是換 loc 資料源(paratranz 中文 loc)+
另寫一份中文結構詞表，不改這裡的規則骨架。
"""
import re
from .parser import Block

_PCT_SUFFIXES = ("_modifier", "_power", "_cost", "_chance", "_speed", "_efficiency")
# 資源點數 effect -> 英文資源名(用於 "Gains N <resource>" / "Loses N <resource>")
_ADD_POWER_MAP = {
    "add_adm_power": "administrative power", "add_dip_power": "diplomatic power",
    "add_mil_power": "military power", "add_prestige": "prestige",
    "add_legitimacy": "legitimacy", "add_stability": "stability",
    "add_manpower": "manpower", "add_treasury": "ducats",
    "add_army_tradition": "army tradition", "add_navy_tradition": "navy tradition",
    "add_devotion": "devotion", "add_horde_unity": "horde unity",
    "add_meritocracy": "meritocracy", "add_absolutism": "absolutism",
    "add_karma": "karma", "add_republican_tradition": "republican tradition",
    "add_papal_influence": "papal influence", "add_yearly_manpower": "yearly manpower",
}
_SCOPE_LABELS = {"root": "our country", "from": "the other country",
                 "prev": "the previous scope", "this": "this scope"}
# 用遊戲自帶英文 loc 模板渲染的命令白名單(人工核對過：模板是乾淨的單值 trigger 句、非假朋友)。
_GAME_LOC_CMDS = {
    "has_reform", "have_had_reform", "primary_culture", "was_tag",
    "mission_completed", "has_terrain", "has_estate_privilege",
    "government_abilities", "has_government_mechanic",
}


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

def _looks_numeric(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


# ---------------- 個別 formatter（一律輸出英文） ----------------

def _fmt_via_game_loc(key, value, loc, tr):
    """白名單命令：用遊戲自帶英文 loc 模板渲染。模板不適用(回 None)時往下一條規則(手寫英文兜底)。"""
    if loc is None or isinstance(value, Block) or _looks_numeric(value):
        return None
    disp = loc.get(value)                 # 值的本地化英文顯示名(如 reform key -> "Mughal Diwan")
    out = loc.game_trigger(key, disp)
    return [out] if out else None

def _fmt_logic(key, value, loc, tr):
    kl = key.lower()
    if not isinstance(value, Block):
        return [f"`{key} = {value}` (logic operator without sub-conditions, see appendix)"]
    sub = tr(value, loc)
    if not sub:
        sub = ["(no sub-conditions)"]
    head = {"and": "All of:", "or": "Any of:"}.get(kl, "None of:")   # not -> None of:
    return [head] + ["  " + s for s in sub]

def _fmt_has_dlc(key, value, loc, tr):
    return [f"Requires DLC: {value}"]

def _fmt_is_core(key, value, loc, tr):
    return [f"Is a core province of {_scope_label(value)}"]

def _fmt_is_claim(key, value, loc, tr):
    return [f"Is claimed by {_scope_label(value)}"]

def _fmt_tag(key, value, loc, tr):
    return [f"Country is {value}"]

def _fmt_add_power(key, value, loc, tr):
    label = _ADD_POWER_MAP.get(key.lower())
    v, num_s = _fmt_number(value)
    if label:
        if str(value).strip().startswith("-"):
            return [f"Loses {num_s.lstrip('-')} {label}"]
        return [f"Gains {num_s} {label}"]
    return [f"{_loc_or_titlecase(key, loc)}: {num_s}"]

def _fmt_gov_attribute(key, value, loc, tr):
    return [f"Government has attribute: {_loc_or_titlecase(value, loc)}"]

def _fmt_flag(key, value, loc, tr):
    return [f"Has internal flag '{value}' (game-internal state, see raw script appendix)"]

def _fmt_set_clr_flag(key, value, loc, tr):
    kl = key.lower()
    verb = "Clears" if kl.startswith("clr_") else "Sets"
    scope = ("province" if "province" in kl else "ruler" if "ruler" in kl
             else "global" if "global" in kl else "country")
    return [f"{verb} {scope} flag '{value}' (game-internal state, see raw script appendix)"]

def _fmt_is_year(key, value, loc, tr):
    return [f"Game year is {value} or later"]

def _fmt_has_reform(key, value, loc, tr):
    return [f"Has enacted government reform: {_loc_or_titlecase(value, loc)}"]

def _fmt_have_had_reform(key, value, loc, tr):
    return [f"Has ever enacted government reform: {_loc_or_titlecase(value, loc)} (not required to still have it)"]

def _fmt_add_government_reform(key, value, loc, tr):
    return [f"Gains government reform: {_loc_or_titlecase(value, loc)}"]

def _fmt_change_government(key, value, loc, tr):
    return [f"Government type changes to {_loc_or_titlecase(value, loc)}"]

def _fmt_country_modifier(key, value, loc, tr):
    kl = key.lower()
    verb = "Removes" if kl.startswith("remove_") else ("Gains" if kl.startswith("add_") else "Has")
    if isinstance(value, Block):
        name = value.get("name") or ""
        return [f"{verb} country modifier: {_loc_or_titlecase(name, loc) if name else '(see appendix)'}"]
    return [f"{verb} country modifier: {_loc_or_titlecase(value, loc)}"]

def _fmt_province_modifier(key, value, loc, tr):
    kl = key.lower()
    verb = "Removes" if kl.startswith("remove_") else ("Gains" if kl.startswith("add_") else "Has")
    if isinstance(value, Block):
        name = value.get("name") or ""
        return [f"{verb} province modifier: {_loc_or_titlecase(name, loc) if name else '(see appendix)'}"]
    return [f"{verb} province modifier: {_loc_or_titlecase(value, loc)}"]

def _fmt_has_building(key, value, loc, tr):
    return [f"Has building: {_loc_or_titlecase(value, loc)}"]

def _fmt_has_estate(key, value, loc, tr):
    return [f"Estate is present: {_loc_or_titlecase(value, loc)}"]

def _fmt_is_subject_of_type(key, value, loc, tr):
    return [f"Subject type is {_loc_or_titlecase(value, loc)}"]

def _fmt_change_unit_type(key, value, loc, tr):
    return [f"Unit type changes to {_loc_or_titlecase(value, loc)}"]

def _fmt_secondary_religion(key, value, loc, tr):
    return [f"Secondary religion is {_loc_or_titlecase(value, loc)}"]

def _fmt_same_continent(key, value, loc, tr):
    return [f"Is on the same continent as {_scope_label(value)}"]

def _fmt_trade_goods(key, value, loc, tr):
    return [f"Trade good is {_loc_or_titlecase(value, loc)}"]

def _fmt_current_age(key, value, loc, tr):
    return [f"Current age is {_loc_or_titlecase(value, loc)}"]

def _fmt_religion(key, value, loc, tr):
    return [f"Religion is {_loc_or_titlecase(value, loc)}"]

def _fmt_government(key, value, loc, tr):
    return [f"Government type is {_loc_or_titlecase(value, loc)}"]

def _fmt_primary_culture(key, value, loc, tr):
    return [f"Primary culture is {_loc_or_titlecase(value, loc)}"]

def _fmt_advisor(key, value, loc, tr):
    return [f"Has advisor: {_loc_or_titlecase(value, loc)}"]

def _fmt_estate_scope(key, value, loc, tr):
    return [f"For estate: {_loc_or_titlecase(value, loc)}"]

def _fmt_share_num(key, value, loc, tr):
    _, num_s = _fmt_number(value)
    return [f"Share change: {num_s}"]

def _fmt_icon(key, value, loc, tr):
    return []  # 純裝飾性圖示檔名，無資訊量，略過(空列表=匹配成功但不輸出)

def _fmt_group_scope(key, value, loc, tr):
    return [f"{_loc_or_titlecase(key, loc)} is {_loc_or_titlecase(value, loc)}"]

def _fmt_bool_flag(key, value, loc, tr):
    yn = "Yes" if str(value).strip().lower() == "yes" else "No"
    return [f"{_loc_or_titlecase(key, loc)}: {yn}"]

def _fmt_custom_tooltip(key, value, loc, tr):
    # custom_trigger_tooltip 包一層 tooltip(說明文字)+實際條件；把實際條件攤平，tooltip 本身略過
    if not isinstance(value, Block):
        return []
    inner_lines = []
    for k, v in value.items:
        if k is not None and k.lower() == "tooltip":
            continue
        inner_lines.extend(tr(Block([(k, v)]), loc))
    return inner_lines or ["(scripted condition, see raw script appendix)"]

def _fmt_limit(key, value, loc, tr):
    sub = tr(value, loc) if isinstance(value, Block) else []
    return ["Limited to:"] + ["  " + s for s in sub] if sub else ["Limited to: (none)"]

def _fmt_number_generic(key, value, loc, tr):
    label = _loc_or_titlecase(key, loc)
    if _is_pct_key(key):
        pct = _fmt_pct(value)
        if pct is not None:
            return [f"{label} {pct}"]
    _, num_s = _fmt_number(value)
    if num_s is not None:
        return [f"{label}: {num_s}"]
    return None  # 非數值，交給下個規則

def _fmt_fallback(key, value, loc, tr):
    if isinstance(value, Block):
        sub = tr(value, loc)
        label = _loc_or_titlecase(key, loc) if loc is not None else key
        if sub:
            return [f"{label} (key: {key}):"] + ["  " + s for s in sub]
        return [f"`{key} = {{}}` (empty block, unmapped — see raw script appendix)"]
    return [f"`{key} = {value}` (unmapped — see raw script appendix)"]


# ---------------- 規則表(matcher, formatter)：按特異性由高到低 ----------------
def _mk(keys):
    keys = set(k.lower() for k in keys)
    return lambda k, v: k.lower() in keys

RULES = [
    (_mk(_GAME_LOC_CMDS), _fmt_via_game_loc),   # 遊戲自帶英文 loc 模板優先(回 None 則落到下面手寫英文)
    (_mk(["and", "or", "not"]), _fmt_logic),
    (_mk(["has_dlc"]), _fmt_has_dlc),
    (_mk(["is_core"]), _fmt_is_core),
    (_mk(["is_claim", "has_claim"]), _fmt_is_claim),
    (_mk(["tag", "original_tag", "overlord", "owner", "controller"]), _fmt_tag),
    (lambda k, v: k.lower() in _ADD_POWER_MAP, _fmt_add_power),
    (_mk(["has_government_attribute"]), _fmt_gov_attribute),
    (lambda k, v: k.lower().startswith(("has_country_flag", "has_ruler_flag", "has_global_flag",
                                         "has_province_flag")), _fmt_flag),
    (lambda k, v: k.lower().startswith(("set_country_flag", "clr_country_flag", "set_province_flag",
                                         "clr_province_flag", "set_ruler_flag", "clr_ruler_flag",
                                         "set_global_flag", "clr_global_flag")), _fmt_set_clr_flag),
    (_mk(["is_year"]), _fmt_is_year),
    (_mk(["has_reform"]), _fmt_has_reform),
    (_mk(["have_had_reform"]), _fmt_have_had_reform),
    (_mk(["add_government_reform"]), _fmt_add_government_reform),
    (_mk(["change_government"]), _fmt_change_government),
    (_mk(["religion", "add_harmonized_religion", "has_reform_religion"]), _fmt_religion),
    (_mk(["secondary_religion"]), _fmt_secondary_religion),
    (_mk(["government"]), _fmt_government),
    (_mk(["primary_culture", "add_accepted_culture", "culture"]), _fmt_primary_culture),
    (_mk(["advisor"]), _fmt_advisor),
    (_mk(["estate"]), _fmt_estate_scope),
    (_mk(["has_estate"]), _fmt_has_estate),
    (_mk(["share"]), _fmt_share_num),
    (_mk(["has_country_modifier", "remove_country_modifier", "add_country_modifier"]), _fmt_country_modifier),
    (_mk(["has_province_modifier", "remove_province_modifier", "add_province_modifier"]), _fmt_province_modifier),
    (_mk(["has_building"]), _fmt_has_building),
    (_mk(["is_subject_of_type"]), _fmt_is_subject_of_type),
    (_mk(["change_unit_type"]), _fmt_change_unit_type),
    (_mk(["same_continent"]), _fmt_same_continent),
    (_mk(["trade_goods"]), _fmt_trade_goods),
    (_mk(["current_age"]), _fmt_current_age),
    (_mk(["custom_trigger_tooltip", "custom_tooltip"]), _fmt_custom_tooltip),
    (_mk(["limit"]), _fmt_limit),
    (_mk(["icon"]), _fmt_icon),
    (lambda k, v: not isinstance(v, Block) and k.lower().endswith("_group"), _fmt_group_scope),
    (_mk(["region", "capital_scope", "colonial_region", "trade_company_region"]), _fmt_group_scope),
    (lambda k, v: not isinstance(v, Block) and _looks_numeric(v), _fmt_number_generic),
    (lambda k, v: not isinstance(v, Block) and str(v).strip().lower() in ("yes", "no"), _fmt_bool_flag),
    (lambda k, v: True, _fmt_fallback),   # 兜底，必定命中
]


def translate_block(block, loc=None):
    """把已展開巨集的 trigger/effect Block 翻譯成人類可讀的「英文」條列文字(List[str])。
    loc: LocalisationIndex 實例(可選)，用於把命令/值轉成遊戲英文顯示名；不給則用 snake_case->Title Case。"""
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
                    out = [f"`{k} = {v}` (translation rule error: {e!r}, see raw script appendix)"]
                if out is None:
                    continue
                lines.extend(out)
                break
    return lines


def translate_block_md(block, loc=None):
    """回傳 Markdown bullet list 字串(供渲染器直接嵌入文件)。"""
    lines = translate_block(block, loc)
    if not lines:
        return "(no translatable conditions)"
    return "\n".join(f"- {l}" if not l.startswith("  ") else l for l in lines)
