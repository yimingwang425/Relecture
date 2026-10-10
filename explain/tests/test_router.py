"""意图路由五类（口径 F119）。

钉三件事：五类各自怎么解析；危机的回复是**固定文案**、模型写什么都不用；
代码兜底只在「路由失败」或「判成 off_topic」时把危机词升为 crisis——判成 course 时不覆盖
（心理学课本来就会讲自杀率）。每条升级的断言旁边都有一条不升的允许控制。
"""

from __future__ import annotations

import re

import pytest

from explain.llm import Completion, Usage
from explain.router import (CRISIS_REPLY, DIRECT_ROUTES, OFF_TOPIC_FALLBACK, ROUTER_SYSTEM, ROUTES,
                            Router)


def router(raw: str) -> Router:
    class M:
        name = "fake:claude-haiku-4-5"

        def complete(self, system, messages):
            return Completion(text=raw, usage=Usage(30, 10), latency_ms=1.0, model=self.name)

    return Router(M())


class TestFiveRoutes:
    @pytest.mark.parametrize("raw,route,direct", [
        ('{"route": "course", "reply": ""}', "course", False),
        ('{"route": "web", "reply": ""}', "web", False),
        ('{"route": "chat", "reply": "不客气！"}', "chat", True),
        ('{"route": "off_topic", "reply": "失恋真的很难受。想先缓一缓也没关系，准备好了我们再接着看特征值。"}',
         "off_topic", True),
    ])
    def test_parse(self, raw, route, direct):
        r = router(raw).route("x")
        assert r.route == route and r.implementation == "model"
        assert r.answers_directly is direct

    def test_off_topic_without_reply_is_invalid_course(self):
        r = router('{"route": "off_topic", "reply": ""}').route("好累")
        assert (r.route, r.implementation) == ("course", "invalid")

    def test_crisis_reply_is_fixed_not_the_models(self):
        r = router('{"route": "crisis", "reply": "加油，多运动就好了！"}').route("活着好没意思")
        assert r.route == "crisis" and r.answers_directly
        assert r.reply == CRISIS_REPLY and "多运动" not in r.reply
        # 用户审定（2026-10-10）：只说拨打心理援助热线，不列号码
        assert "心理援助热线" in CRISIS_REPLY and not re.search(r"\d", CRISIS_REPLY)

    def test_preachy_off_topic_reply_is_replaced(self):
        """真模型实测（2026-10-10）：「考砸了」那一句的回复带了「先别急着否定自己」——那是在劝。"""
        r = router('{"route": "off_topic", "reply": "考试没考好确实难受。不过先别急着否定自己，我们继续学。"}').route("考砸了")
        assert (r.route, r.implementation, r.reply) == ("off_topic", "guarded", OFF_TOPIC_FALLBACK)
        ok = router('{"route": "off_topic", "reply": "失恋真的很难受。想先缓一缓也没关系，准备好了我们再接着看特征值。"}')
        assert ok.route("失恋了").implementation == "model"      # 允许控制：不劝的回复原样保留

    def test_web_is_not_a_direct_answer(self):
        assert "web" in ROUTES and "web" not in DIRECT_ROUTES


class TestCrisisSafetyNet:
    def test_router_failure_with_crisis_words_becomes_crisis(self):
        r = router("胡言乱语").route("压力大到不想活了")
        assert (r.route, r.implementation, r.reply) == ("crisis", "keyword", CRISIS_REPLY)

    def test_off_topic_with_crisis_words_is_upgraded(self):
        r = router('{"route": "off_topic", "reply": "听起来很累。"}').route("真想一了百了")
        assert r.route == "crisis" and r.reply == CRISIS_REPLY

    def test_course_is_not_overridden(self):
        """允许控制：心理学课问自杀率，模型判 course 就是 course。"""
        r = router('{"route": "course", "reply": ""}').route("课件第 3 页的自杀率数据是怎么统计的？")
        assert r.route == "course"

    def test_failure_without_crisis_words_stays_course(self):
        """允许控制：失败本身不升级——否则「一律升」也会让上面全绿。"""
        r = router("胡言乱语").route("压力好大，好崩溃")
        assert r.route == "course"


class TestPromptDrawsTheLine:
    """off_topic 与 crisis 是连续的光谱：边界要用**成对的例子**画在 prompt 里。"""

    @pytest.mark.parametrize("line", [
        "| 压力好大，好崩溃 | off_topic",
        "| 压力大到不想活了 | crisis |",
        "| 失恋了，什么都学不进去 | off_topic |",
        "| 活着好没意思 | crisis |",
    ])
    def test_boundary_examples_present(self, line):
        assert line in ROUTER_SYSTEM

    def test_off_topic_reply_does_not_preach(self):
        assert "不给建议、不讲道理" in ROUTER_SYSTEM
        assert "想先缓一缓也没关系" in ROUTER_SYSTEM

    def test_web_requires_explicit_ask(self):
        assert "只是课件里可能没有答案，不算 web" in ROUTER_SYSTEM
        # 真模型实测（2026-10-10，r2）：这一句被判成了 web。r3 把它写成反例。
        assert "「特征值在量子力学里是什么意思？」" in ROUTER_SYSTEM
