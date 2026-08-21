"""词法工具：停用词、词形归一、候选短语。

**不引入 NLP 依赖。** 名词短语用「大写模式 + n-gram + 停用词表 + 复数归一」近似。
这是保守近似，会漏真术语——**符合 PRD §8.1 的误差方向**：
遗漏项不进分母，只降低指标灵敏度；误收普通词会导致审查误判。两者不对称。

决策日期：2026-08-21
"""

from __future__ import annotations

import re

__all__ = [
    "STOPWORDS",
    "COMMON_WORDS",
    "MAX_PHRASE_WORDS",
    "tokenize",
    "singularize",
    "normalize_phrase",
    "is_unit_or_number",
    "candidate_phrases",
    "has_sentence_punct",
]

#: 短语长度上限。实测 v1.1 最长术语为 3 词（`community inclusion currency` 类），
#: 图表页块最长 5 词。取 3：更长的几乎必然是整句而非术语。
MAX_PHRASE_WORDS = 3

STOPWORDS = frozenset("""
a an the and or but if then than that this these those of in on at to for from by with
without within into onto over under about above below between among through during
is are was were be been being am do does did done have has had having will would shall
should can could may might must not no nor only just also very more most much many few
such same other another each every all any some both either neither as it its it's they
them their there here when where which who whom whose what why how so too still yet
we you your our us i he she his her him one two three when's let lets get gets
""".split())

#: G1（领域限定性）的近似：**通用英语高频词表**。
#:
#: 判据原文举的反例是 `energy` `water` `cost`。实测校准依据：金标准 44 条里
#: **35 条是双词短语**，8 条单词术语全部是领域词
#: （biomass / photovoltaic / alternator / condenser / coolant / furnace /
#: pressurizer / turbine）——**没有一个是 heat、steam、water、coal 这类**。
#:
#: 故本表收录通用英语的核心高频词：**单词候选凡在表内一律拒绝**，
#: 多词组合只在**全部**成词都在表内时拒绝（`energy density` 收，`energy` 不收）。
#: 这是频次的人工近似，不是语料统计——**宁可漏掉真术语，也不放进普通词**。
#: 已知代价：`heat engine` 这类「常见词 + 常见词」的真术语会被误拒，
#: 见 README 的召回率缺口分析。
COMMON_WORDS = frozenset("""
about above across act action add after again against age air all allow almost alone
along already also always among amount and animal another answer any appear apply area
arm around arrive art ask attack available away back bad bag ball bank base be beautiful
because become bed before begin behind believe below beside best better between big bill
bit black block blood blue board boat body book born both bottom box boy break bring
broad brother build burn business but buy by call can car care carry case catch cause
cell center central century certain chance change chapter character charge check child
choose church city claim class clean clear close coal coast cold collect color come
common community company compare complete computer condition consider contain continue
control cook copy corner cost could count country couple course cover create cross cry
current cut dark data date day dead deal death decide deep degree describe design detail
develop die difference different difficult direct discuss distance do doctor dog door
double doubt down draw dream drink drive drop dry during each early earth ease east easy
eat economy edge effect effort egg either electricity else end energy engine enough enter
environment equal especially even event ever every exact example except exist expect
experience explain eye face fact factor fail fall family far farm fast father favor fear
feed feel few field fight figure fill film final find fine finger finish fire firm first
fish fit five fix floor flow fly focus follow food foot for force forest forget form
former forward four free fresh friend from front fuel full function future gain game
garden gas general get gift girl give glass go goal gold good government great green
ground group grow guess gun hair half hand hang happen happy hard have he head health
hear heart heat heavy help her here high hill him history hit hold hole home hope horse
hospital hot hour house how however huge human hundred husband ice idea identify if
image imagine impact important improve in include increase indeed indicate industry
inside instead interest into introduce involve issue it item job join just keep key kid
kill kind king know land language large last late later laugh law lay lead learn leave
left leg less let letter level lie life light like limit line list listen little live
local long look lose loss lot love low machine main maintain major make man manage many
map mark market marry material matter may maybe mean measure media meet member memory
mention message method middle might mile military million mind minute miss model modern
moment money month moon more morning most mother mountain mouth move much music must
name nation natural nature near necessary need never new news next nice night no none
nor north not note nothing notice now number occur ocean of off offer office official
often oil old on once one only open operate opportunity option or order organization
other our out outside over own page pain paint paper parent part particular partner pass
past pattern pay peace people per perform perhaps period person phone physical pick
picture piece place plan plant play please point police policy political poor popular
population position possible power practice prepare present press pressure pretty
prevent price private probably problem process produce product program project property
protect prove provide public pull purpose push put quality question quickly quite race
radio raise range rate rather reach read ready real reason receive recent recognize
record red reduce reflect region relate relationship remain remember remove report
represent require research resource respond response responsibility rest result return
reveal rich right rise risk river road rock role room rule run safe same save say scene
school science score sea season seat second section security see seek seem sell send
sense series serious serve service set seven several sex shake share she ship shoot shop
short shot should shoulder show side sign significant similar simple simply since sing
single sister sit site situation six size skill skin small smile so social society
soldier some son song soon sort sound source south space speak special specific speed
spend sport spring staff stage stand standard star start state station stay step still
stock stop store story street strong structure student study stuff style subject success
such suddenly suffer suggest summer sun supply support sure surface system table take
talk task tax teach teacher team technology television tell ten tend term test than
thank that the their them themselves then theory there these they thing think third this
those though thought thousand three through throw thus time to today together too top
total touch toward tower town trade traditional traffic training travel treat tree trial
trip trouble true trust truth try turn two type under understand unit until up upon use
user usual usage value various very victim view visit voice vote wait walk wall want war
watch water way we wear week weight well west what when where whether which while white
who whole why wide wife will win wind window wine winter wish with within without woman
wonder word work world worry worth would write wrong yard yeah year yes yet you young
your yourself
steam vessel rod core loop application breakdown wave cool cooling heating
generator pump vapor liquid solid content peak charge storage usage
transportation electric electrical supply demand resource technology consumption
reserve production waste renewable conservation efficiency emission pollution
""".split())

#: G5（非专有名词）的近似：机构 / 项目 / 地名 / 人名的已知片段。
PROPER_NOUN_HINTS = frozenset("""
mit ocw ocean opencourseware eia doe psfc nasa epa iea bp wikimedia flickr commons
usda nrcs eere sandia labs cambridge press goldstein legare street reserve bank
boston kansas city fed bis slate krugman nakamoto townsend samphantharak lim
schuh pawasutipaisit alvarez amberg jacobson schedvin jain wang bech garratt shin
ingves safaricom kenya kenyan sweden india china russia australia arabia saudi
quebec shangai bombay cape wind rohde betts cotte dattodesign lippert rainer
robert john andrew michael golay lynn andré xavier briggs campbell fainstein
""".split())

#: 连字归一。PDF 里 ﬁ/ﬂ 等连字是单个码位，不归一会把 difficult 切成 dif + cult。
#: 这是**缺陷修复**不是口径选择——连字与它拼出的字母序列是同一个词。
_LIGATURES = str.maketrans({
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
})

_TOKEN = re.compile(r"[A-Za-z][A-Za-z\-']*")
#: 单位、化学式、纯符号 —— 判据 G4 明确不进术语表。
_UNIT = re.compile(
    r"^(?:"
    r"\d+(?:[.,]\d+)*"
    r"|[munkKMGT]?(?:W|Wh|J|Pa|V|A|Hz|m|m2|m3|g|t|K|C|F|B|bar|toe|btu)"
    r"|°?[CF]|%|\$|€|£"
    r"|[A-Z][a-z]?\d+[A-Za-z0-9]*"          # CO2, CH4, 235U, C8H18
    r"|\d+[A-Za-z]+"                          # 235U
    r")$"
)
_SENT_PUNCT = re.compile(r"[.!?;:]\s*$|[.!?;]\s")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.translate(_LIGATURES))


def singularize(word: str) -> str:
    """极简复数归一。只处理高置信规则，**不猜**。

    v1.1 里 4 条正文术语在课件中只以复数/派生形出现
    （`breeders` / `fission products` / `enrichment`），归一是提高召回的必要一步。
    """
    w = word.lower()
    if len(w) <= 3 or w.endswith("ss") or w.endswith("us") or w.endswith("is"):
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("ches", "shes", "sses", "xes", "zes")):
        return w[:-2]
    if w.endswith("s"):
        return w[:-1]
    return w


def normalize_phrase(text: str) -> str:
    """归一化为匹配键：小写 + 复数归一 + 单空格。"""
    return " ".join(singularize(t) for t in tokenize(text))


def is_unit_or_number(token: str) -> bool:
    return bool(_UNIT.match(token.strip()))


def has_sentence_punct(text: str) -> bool:
    return bool(_SENT_PUNCT.search(text.strip()))


def candidate_phrases(text: str, max_words: int = MAX_PHRASE_WORDS) -> list[str]:
    """从一段文本里取候选短语：连续的非停用词序列，长度 1..max_words。

    停用词与标点都作为边界——跨过它们的组合不是名词短语。
    """
    text = text.translate(_LIGATURES)
    out: list[str] = []
    for run in re.split(r"[^A-Za-z\-'\s]+", text):
        toks = tokenize(run)
        group: list[str] = []
        for tok in toks:
            if tok.lower() in STOPWORDS:
                out.extend(_windows(group, max_words))
                group = []
            else:
                group.append(tok)
        out.extend(_windows(group, max_words))
    return out


def _windows(tokens: list[str], max_words: int) -> list[str]:
    out: list[str] = []
    for n in range(1, max_words + 1):
        for i in range(len(tokens) - n + 1):
            out.append(" ".join(tokens[i : i + n]))
    return out
