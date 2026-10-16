import unittest
from core import StableEmitter, route_workflow, SessionState, validate_bindings


class StreamingTests(unittest.TestCase):
    def test_incomplete_chinese_byte_token_waits_for_the_real_character(self):
        emitter = StableEmitter()
        output = ""
        for result in ["全球金融动�", "全球金融动�", "全球金融动�的", "全球金融动荡的", "全球金融动荡的表现"]:
            output += emitter.accept(result)
            self.assertNotIn("\ufffd", output)
        output += emitter.accept("全球金融动荡的表现", final=True)
        self.assertEqual(output, "全球金融动荡的表现")
        self.assertFalse(emitter.conflict)

    def test_live_prefix_before_final(self):
        emitter = StableEmitter()
        self.assertEqual(emitter.accept("今天"), "")
        self.assertEqual(emitter.accept("今天天气"), "今天")
        self.assertEqual(emitter.accept("今天天气很好"), "天气")
        self.assertEqual(emitter.accept("今天天气很好", final=True), "很好")
        self.assertEqual(emitter.accept("今天天气很好", final=True), "")

    def test_revision_never_deletes_or_duplicates(self):
        emitter = StableEmitter()
        emitter.accept("我想买")
        self.assertEqual(emitter.accept("我想买书"), "我想买")
        self.assertEqual(emitter.accept("我想卖书", final=True), "")
        self.assertTrue(emitter.conflict)
        self.assertEqual(emitter.accept("我想买很多书", final=True), "")

    def test_edit_then_new_sentence_cannot_restore_old_text(self):
        first = StableEmitter()
        output = first.accept("明天去上海", final=True)
        output = output.replace("上海", "北京")  # a user's edit is outside ASR ownership
        next_segment = StableEmitter()
        output += next_segment.accept("然后坐地铁", final=True)
        self.assertEqual(output, "明天去北京然后坐地铁")


class RoutingTests(unittest.TestCase):
    def test_conversational_workflow_aliases(self):
        cases = {"让GPT开始制作色卡":"palette", "帮我用GPT生图":"image",
                 "我想让龙虾生成一张图片":"image", "开启色卡模式":"palette",
                 "进入生图模式":"image", "开始制作配色板":"palette", "生成一张色彩卡":"palette",
                 "色卡生成，冷色调":"palette", "图片制作，一只小鸟":"image",
                 "画一幅插画":"image", "出一张海报":"image", "找找参考图":"reference",
                 "搜一搜参考图":"reference", "切换到参考图模式":"reference"}
        cases.update({"给我准备几张电图":"reference", "我们先搞各色卡颜色温暖一点":"palette",
                      "这让这批可以帮我生成图片一片蓝色的海":"image"})
        for text, mode in cases.items():
            with self.subTest(text=text):self.assertEqual(route_workflow(text).mode, mode)

    def test_palette_variants(self):
        cases = ["制作色卡", "开始制作色卡", "色卡制作", "现在开始色卡制作", "生成一张色卡",
                 "帮我做一张色卡", "我想要制作色卡", "给我弄一套色卡", "搞一张配色卡",
                 "请设计调色板", "我们来做个色卡", "帮我把这张图做成色卡", "从图片中提取色卡",
                 "开始制作一张色卡", "根据参考图生成配色方案", "先不要生成图片，但是我要制作色卡",
                 "不要生成图片，帮我制作色卡", "我不是要生成图片而是要制作色卡",
                 "嗯那个我们开始制作色卡，冷色调", "不用生图我要制作色卡"]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(route_workflow(text).mode, "palette")

    def test_image_and_reference_variants(self):
        for text in ["生成图片", "图片生成", "开始生图", "帮我画一张图", "我要生成一张图片，人物穿蓝色衣服"]:
            with self.subTest(text=text):
                self.assertEqual(route_workflow(text).mode, "image")
        for text in ["找一些参考图", "整理参考图片", "参考图", "帮我准备几张垫图", "使用参考图"]:
            with self.subTest(text=text):
                self.assertEqual(route_workflow(text).mode, "reference")

    def test_negation_and_body_are_not_commands(self):
        cases = ["不要生成图片", "先不制作色卡", "我不想制作色卡", "请讲讲色卡制作", "色卡是什么",
                 "我今天看到一张色卡很漂亮", "提示词是开始制作色卡", "怎么生成图片", "这次不用生图",
                 "我说的是不要制作色卡", "介绍一下参考图的用途"]
        cases += ["不要准备电图", "给我解释心电图报告", "我听说有人帮我生成图片",
                  "这句话是让GPT帮我生成图片", "让GPT帮我讲讲如何生成图片"]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(route_workflow(text).mode, "unmatched")

    def test_ambiguous_is_not_dispatched(self):
        self.assertEqual(route_workflow("制作色卡，生成图片").mode, "ambiguous")
        self.assertEqual(route_workflow("生成图片，内容是一个人正在制作色卡").mode, "image")


class SessionTests(unittest.TestCase):
    def test_same_button_finishes_and_other_does_not_switch(self):
        s = SessionState()
        self.assertEqual(s.press("dictation"), "start")
        self.assertEqual(s.press("workflow"), "busy")
        self.assertEqual(s.mode, "dictation")
        self.assertEqual(s.press("dictation"), "stop")
        self.assertEqual(s.mode, "idle")

    def test_continuous_pauses_resumes_until_explicit_finish(self):
        s = SessionState()
        self.assertEqual(s.press("dictation", True), "start")
        generation = s.generation
        self.assertEqual(s.press("dictation", True), "pause")
        self.assertEqual(s.generation, generation)  # accept final tail before pausing
        self.assertEqual(s.mode, "dictation")
        self.assertEqual(s.press("dictation", True), "resume")
        self.assertGreater(s.generation, generation)
        s.finish()
        self.assertEqual(s.mode, "idle")

    def test_invalid_bindings(self):
        for pair in [("x1", "x1"), ("ctrl+shift+space", "shift+ctrl+space"), ("ctrl+ctrl+space", "x2"), ("alt", "lalt"), ("abc", "x2")]:
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                validate_bindings(*pair)
        self.assertEqual(validate_bindings(" F8 ", "X2"), ("f8", "x2"))


if __name__ == "__main__":
    unittest.main()
