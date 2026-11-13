"""tools/build_names.py: the names list from Wikidata (offline parts only)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from build_names import character_rows, name_of, parse_characters, reading_of, select, taiwan  # noqa: E402

TO_TW = str.maketrans({"伦": "倫", "条": "條", "伟": "偉", "演": "演", "员": "員"})


def convert(text):  # (a stand-in for OpenCC s2tw, enough for these names)
    return text.translate(TO_TW)


def item(tw=None, hant=None, zh=None, title=None, sl=1):
    return {"tw": tw, "hant": hant, "zh": zh, "title": title, "sl": sl}


class NameOfTest(unittest.TestCase):
    def test_priority_and_conversion(self):
        self.assertEqual(name_of(item(tw="五條悟", hant="五條悟x"), convert), "五條悟")
        self.assertEqual(name_of(item(hant="秋本治", zh="秋本治x"), convert), "秋本治")
        self.assertEqual(name_of(item(zh="周杰伦"), convert), "周杰倫")
        self.assertEqual(name_of(item(title="五条悟"), convert), "五條悟")

    def test_note_dropped(self):
        self.assertEqual(name_of(item(hant="王偉 (演員)"), convert), "王偉")
        self.assertEqual(name_of(item(zh="王伟（演员）"), convert), "王偉")

    def test_li_inside_is_li_village(self):
        # (戴克裡先, 本田美登裡 in Wikidata's Traditional labels: 裡 "inside" is never a name's 里)
        self.assertEqual(name_of(item(hant="戴克裡先"), convert), "戴克里先")
        self.assertEqual(name_of(item(tw="本田美登裡"), convert), "本田美登里")

    def test_not_a_name_we_take(self):
        self.assertIsNone(name_of(item(tw="萊納爾·梅西"), convert))  # (a Western name: not in 3.2.2)
        self.assertIsNone(name_of(item(tw="王"), convert))
        self.assertIsNone(name_of(item(tw="一二三四五六七"), convert))
        self.assertIsNone(name_of(item(tw="S.H.E"), convert))
        self.assertIsNone(name_of(item(), convert))


class TaiwanTest(unittest.TestCase):
    # stand-ins for OpenCC: s2tw turns 里 into 裡 and 余 into 餘 the way it does for a single character
    S2TW = str.maketrans({"亚": "亞", "里": "裡", "伦": "倫", "余": "餘", "乐": "樂", "范": "範", "于": "於"})
    T2S = str.maketrans({"亞": "亚", "裡": "里", "倫": "伦", "餘": "余", "樂": "乐", "範": "范", "於": "于"})

    def taiwan(self, text):
        return taiwan(text, lambda t: t.translate(self.S2TW), lambda t: t.translate(self.T2S))

    def test_simplified_converted(self):
        self.assertEqual(self.taiwan("周杰伦"), "周杰倫")

    def test_traditional_left_alone(self):
        # (亞里士多德, already Traditional, came out 亞裡士多德 when converted again)
        self.assertEqual(self.taiwan("亞里士多德"), "亞里士多德")

    def test_surnames_kept(self):
        # (余 范 于 in a name are surnames, never 餘 範 於)
        self.assertEqual(self.taiwan("余文乐"), "余文樂")
        self.assertEqual(self.taiwan("范伟"), "范伟".translate(self.S2TW).replace("範", "范"))
        self.assertEqual(self.taiwan("于谦"), "于谦")


class ReadingTest(unittest.TestCase):
    def test_reading(self):
        best = {"田": "ㄊㄧㄢˊ", "馥": "ㄈㄨˋ", "甄": "ㄓㄣ"}
        self.assertEqual(reading_of("田馥甄", best), "ㄊㄧㄢˊ-ㄈㄨˋ-ㄓㄣ")
        self.assertIsNone(reading_of("田馥珍", best))


class SelectTest(unittest.TestCase):
    BEST = {"田": "ㄊㄧㄢˊ", "馥": "ㄈㄨˋ", "甄": "ㄓㄣ", "秋": "ㄑㄧㄡ", "本": "ㄅㄣˇ", "治": "ㄓˋ",
            "周": "ㄓㄡ", "杰": "ㄐㄧㄝˊ", "倫": "ㄌㄨㄣˊ"}

    def test_dedup_known_unreadable_and_top(self):
        items = [item(tw="秋本治", sl=15), item(tw="田馥甄", sl=3), item(tw="田馥甄", sl=29),
                 item(tw="周杰倫", sl=90), item(tw="田馥珍", sl=50)]
        got = select(items, {"周杰倫"}, self.BEST, convert, top=10)
        self.assertEqual(got, [("田馥甄", "ㄊㄧㄢˊ-ㄈㄨˋ-ㄓㄣ", 29), ("秋本治", "ㄑㄧㄡ-ㄅㄣˇ-ㄓˋ", 15)])
        self.assertEqual(select(items, {"周杰倫"}, self.BEST, convert, top=1), [("田馥甄", "ㄊㄧㄢˊ-ㄈㄨˋ-ㄓㄣ", 29)])

    def test_ties_by_name(self):
        items = [item(tw="秋本治", sl=5), item(tw="田馥甄", sl=5)]
        self.assertEqual([n for n, _, _ in select(items, set(), self.BEST, convert)], sorted(["秋本治", "田馥甄"]))


class ParseCharactersTest(unittest.TestCase):
    """The characters of a Chinese Wikipedia 角色列表 article (#14; pieces of the articles as of 2026-11-13):
    each a list of ways it is written, the main one first; 姓|名 for a Japanese name, · in a foreign one."""

    def test_nihongo_with_family_given_split(self):
        text = "; {{Nihongo|潔世一|潔 世一（いさぎ よいち）|聲：[[浦和希]]}}\n: 本作主角。"
        self.assertEqual(parse_characters(text), [["潔|世一"]])

    def test_japanese_forms_differ_split_by_count(self):
        text = "; {{Nihongo|劍城斬鐵|剣城 斬鉄（つるぎ ざんてつ）|聲：[[興津和幸]]}}"
        self.assertEqual(parse_characters(text), [["劍城|斬鐵"]])

    def test_counts_differ_whole_name_only(self):
        text = "; {{Nihongo|帝襟杏里|帝襟 アンリ（ていえり アンリ）|聲：[[幸村惠理]]}}"
        self.assertEqual(parse_characters(text), [["帝襟|杏里"]])
        text = "; {{Nihongo|蛇來彌勒|陀来 弥勒（だらい みろく）|聲：[[白井悠介]]}}"
        self.assertEqual(parse_characters(text), [["蛇來|彌勒"]])
        text = "; {{Nihongo|石狩幸雄|石狩幸雄（いしかり ゆきお）|聲：[[盆子原康]]}}"
        self.assertEqual(parse_characters(text), [["石狩幸雄"]])
        text = "; {{Nihongo|冰織羊|氷織羊(ひおり よう)|聲：[[三上瑛士]]}}"
        self.assertEqual(parse_characters(text), [["冰織羊"]])

    def test_jpn_template(self):
        text = "; {{anchor|我妻善逸}}{{Jpn|lead=no|c=我妻善逸|j=我妻 善逸|hg=あがつま ぜんいつ|rm=Agatsuma}}"
        self.assertEqual(parse_characters(text), [["我妻|善逸"]])

    def test_dotted_name_and_anchor_aliases(self):
        text = ";{{Anchor|艾連·葉卡|艾連}}{{nihongo|[[艾連·葉卡]]|エレン・イェーガー|Eren Yeager}}"
        self.assertEqual(parse_characters(text), [["艾連·葉卡", "艾連"]])

    def test_link_with_label_and_aliases(self):
        text = "; {{anchor|雷姆|蕾姆}}{{nihongo|[[雷姆 (角色)|雷姆]]|レム|Rem|聲：[[水瀨祈]]}}"
        self.assertEqual(parse_characters(text), [["雷姆", "蕾姆"]])

    def test_animax_translation_in_notetag(self):
        text = "; {{anchor|奧托·思文|奧托}}{{nihongo|奧托·思文{{NoteTag|[[ANIMAX]]譯為奧特·思文}}|オットー・スーウェン}}"
        self.assertEqual(parse_characters(text), [["奧托·思文", "奧托", "奧特·思文"]])

    def test_two_names_of_one_character(self):
        text = (";{{Nihongo|希露菲葉特|シルフィエット|Sylphiette}}／"
                "{{Nihongo|希露菲葉特·格雷拉特|シルフィエット・グレイラット}}")
        self.assertEqual(parse_characters(text), [["希露菲葉特", "希露菲葉特·格雷拉特"]])

    def test_two_people_on_one_line(self):
        text = "; {{Nihongo|潔一生、潔伊世|潔 一生（いさぎ いっせい）、潔 伊世（いさぎ いよ）|聲：[[x]]}}"
        self.assertEqual(parse_characters(text), [["潔|一生"], ["潔|伊世"]])

    def test_letters_kept_in_written(self):
        text = ";{{Anchor|羅茲瓦爾·L·梅札斯|羅茲瓦爾}}{{nihongo|羅茲瓦爾·L·梅札斯|ロズワール・L・メイザース}}"
        self.assertEqual(parse_characters(text), [["羅茲瓦爾·L·梅札斯", "羅茲瓦爾"]])

    def test_dots_made_one(self):
        text = "; {{Nihongo|奧利佛‧愛空|オリヴァ・愛空（オリヴァ あいく）}}"
        self.assertEqual(parse_characters(text), [["奧利佛·愛空"]])

    def test_kana_name_whole(self):
        text = ";* {{nihongo|[[宇智波佐助]]|うちはサスケ|聲：[[杉山紀彰]]}}"
        self.assertEqual(parse_characters(text), [["宇智波佐助"]])

    def test_kanji_family_kana_given(self):
        text = "{{nihongo|碇真嗣|碇シンジ|Ikari Shinji|聲優：[[緒方惠美]]}} （又译'''碇真治'''）"
        self.assertEqual(parse_characters(text), [["碇|真嗣"]])

    def test_chinese_before_jpn(self):
        text = "; 野原廣志（{{jpn|j=野原ひろし |rm= Nohara Hiroshi|link=no}}）\n; 風間徹（{{jpn|j= 風間 トオル|rm=Kazama Tōru|link=no}}）"
        self.assertEqual(parse_characters(text), [["野原|廣志"], ["風間|徹"]])

    def test_heading_with_main_article(self):
        text = "=== 阿爾馮斯·艾力克 ===\n{{main|艾爾凡斯·艾力克}}\n=== 王騎軍 ===\n: 說明"
        self.assertEqual(parse_characters(text), [["阿爾馮斯·艾力克"]])

    def test_visible_anchor(self):
        text = "; {{Nihongo|{{Visible anchor|日向秀樹}}|日向 秀樹（ひなた ひでき）|聲優：[[木村良平]]}}"
        self.assertEqual(parse_characters(text), [["日向|秀樹"]])

    def test_name_with_kana_reading(self):
        text = (";「奥州筆頭」「一刀龍爪」 [[伊達政宗]]（だて まさむね）\n"
                "!colspan=\"2\"|'''櫻桃子'''（{{lang|ja|さくら ももこ}}）Sakura Momoko")
        self.assertEqual(parse_characters(text), [["伊達政宗"], ["櫻桃子"]])

    def test_heading_of_a_group_skipped(self):
        # (BLEACH: groups and episodes with an article of their own under a heading; a name there has a ·)
        text = "=== 假面軍勢 ===\n{{main|假面軍勢}}\n=== 呼喚你的名字 ===\n{{main|呼喚你的名字}}"
        self.assertEqual(parse_characters(text), [])

    def test_no_name_has_de(self):
        text = "; {{nihongo|惡魔的童話花牌|悪魔のメルヘンカルタ}}"
        self.assertEqual(parse_characters(text), [])

    def test_bare_line_skipped(self):
        self.assertEqual(parse_characters(";大空翼\n; 共同的設定\n; 漫畫及動畫第二作設定："), [])

    def test_skips_descriptions(self):
        text = "; {{Nihongo|一難高中教練|一難高校監督|聲：[[小山力也]]}}\n; {{Nihongo|玲王的父母|玲王の両親}}"
        self.assertEqual(parse_characters(text), [])

    def test_unknown_markup_skipped(self):
        self.assertEqual(parse_characters("{| class=wikitable\n| 潔世一 || x\n|}\n; 沒有模板的一行\n; {{Nihongo|"),
                         [])


class FamilyNamesTest(unittest.TestCase):
    """A Japanese name the list gives no split for (うちはサスケ) split by the family name others of the same
    article start with (宇智波佐助, 宇智波鼬: 宇智波|佐助) - re-check 2026-11-13: 宇智波 led to no 佐助."""

    def test_shared_family(self):
        from build_names import split_families
        text = "\n".join(f"; {{{{nihongo|{c}|{j}}}}}" for c, j in (
            ("宇智波佐助", "うちはサスケ"), ("宇智波鼬", "うちはイタチ"), ("春野櫻", "春野サクラ"),
            ("漩渦鳴人", "うずまきナルト"), ("漩渦玖辛奈", "うずまきクシナ")))
        got = split_families(parse_characters(text))
        self.assertEqual(got, [["宇智波|佐助"], ["宇智波|鼬"], ["春野|櫻"], ["漩渦|鳴人"], ["漩渦|玖辛奈"]])

    def test_shared_start_leaves_a_given_name_of_two(self):
        # (review 2026-11-13: 緋村劍|心, 緋村劍|路 - the longest shared start left only one-character given names;
        # 練紅|炎 and 李青|舜 are one-character family names: whole)
        from build_names import JapaneseName as J, split_families
        got = split_families([[J("緋村劍心")], [J("緋村劍路")], [J("緋村彌彥")], [J("練紅炎")], [J("練紅明")],
                              [J("李青舜")], [J("李青秀")]])
        self.assertEqual(got, [["緋村|劍心"], ["緋村|劍路"], ["緋村|彌彥"], ["練紅炎"], ["練紅明"], ["李青舜"], ["李青秀"]])

    def test_list_family_first_and_whole_family_kept(self):
        from build_names import JapaneseName as J, split_families
        self.assertEqual(split_families([["竈門|炭治郎"], [J("竈門炭十郎")], [J("竈門炭吉")]]),
                         [["竈門|炭治郎"], ["竈門|炭十郎"], ["竈門|炭吉"]])
        self.assertEqual(split_families([[J("宇智波")], [J("宇智波佐助")], [J("宇智波鼬")]]),
                         [["宇智波"], ["宇智波|佐助"], ["宇智波|鼬"]])
        self.assertEqual(split_families([["宇智波|佐助"], [J("宇智波")]]), [["宇智波|佐助"], ["宇智波"]])

    def test_iteration_mark_in_the_family(self):
        text = "; {{Nihongo|佐佐岡良子|佐々岡 よし子（ささおか よしこ）}}"
        self.assertEqual(parse_characters(text), [["佐佐岡|良子"]])

    def test_foreign_names_not_split(self):
        # (katakana only: no family and given name - 阿爾札克 and 阿爾瑪 only start alike)
        from build_names import split_families
        text = "; {{nihongo|阿爾札克|アルザック}}\n; {{nihongo|阿爾瑪|アルマ}}"
        self.assertEqual(split_families(parse_characters(text)), [["阿爾札克"], ["阿爾瑪"]])

    def test_split_or_dotted_names_left_alone(self):
        from build_names import JapaneseName, split_families
        got = split_families([["竈門|炭治郎"], [JapaneseName("竈門禰豆子")], ["艾連·葉卡"], ["艾連·克魯格"], ["我愛羅"]])
        self.assertEqual(got, [["竈門|炭治郎"], ["竈門|禰豆子"], ["艾連·葉卡"], ["艾連·克魯格"], ["我愛羅"]])

    def test_two_characters_whole_name_no_split(self):
        # (a name all of whose characters another starts with is no family: 佐助 and 佐助丸)
        from build_names import JapaneseName, split_families
        self.assertEqual(split_families([[JapaneseName("佐助")], [JapaneseName("佐助丸")]]), [["佐助"], ["佐助丸"]])


class CharacterRowsTest(unittest.TestCase):
    BEST = {c: f"r{c}" for c in "潔世一艾連葉卡竈門炭治郎羅茲瓦爾梅札斯"}

    def rows(self, characters, known=()):
        return character_rows(characters, set(known), self.BEST, lambda t: t)

    def test_rows(self):
        self.assertEqual(self.rows([["潔|世一"], ["艾連·葉卡", "艾連"]]),
                         [("r潔-r世-r一", "潔世一", "潔|世一"), ("r世-r一", "世一", "世一"),
                          ("r艾-r連-r葉-r卡", "艾連葉卡", "艾連·葉卡"), ("r艾-r連", "艾連", "艾連"),
                          ("r葉-r卡", "葉卡", "葉卡")])

    def test_known_segment_not_its_own_row(self):
        self.assertEqual(self.rows([["竈門|炭治郎"]], {"炭治郎"}),
                         [("r竈-r門-r炭-r治-r郎", "竈門炭治郎", "竈門|炭治郎"), ("r竈-r門", "竈門", "竈門")])

    def test_known_single_segment_name_skipped(self):
        self.assertEqual(self.rows([["炭治郎"]], {"炭治郎"}), [])

    def test_letters_dropped_from_candidate(self):
        self.assertEqual(self.rows([["羅茲瓦爾·L·梅札斯"]])[0],
                         ("r羅-r茲-r瓦-r爾-r梅-r札-r斯", "羅茲瓦爾梅札斯", "羅茲瓦爾·L·梅札斯"))

    def test_unreadable_skipped(self):
        self.assertEqual(self.rows([["潔|世二"]]), [])

    def test_rows_dedupe_keep_first(self):
        self.assertEqual([w for _, _, w in self.rows([["艾連·葉卡"], ["艾連·葉卡", "艾連"]])],
                         ["艾連·葉卡", "艾連", "葉卡"])

    def test_wikipedia_taiwan_names(self):
        # (the article's names as Wikipedia shows them in Taiwan, its NoteTA applied: 路飛 is 魯夫 there)
        from build_names import as_shown
        table = {"蒙其·D·路飛": "蒙其·D·魯夫", "竈門炭治郎": "竈門炭治郎", "灶門禰豆子": "竈門禰豆子",
                 "長谷川泰三": "長谷川泰"}
        self.assertEqual(as_shown("蒙其·D·路飛", table), "蒙其·D·魯夫")
        self.assertEqual(as_shown("灶門|禰豆子", table), "竈門|禰豆子")
        self.assertEqual(as_shown("長谷川|泰三", table), "長谷川泰")  # (another length: the split is lost)
        self.assertEqual(as_shown("潔|世一", table), "潔|世一")

    def test_japanese_forms_as_taiwan_writes(self):
        from build_names import VARIANTS
        self.assertEqual("蜂樂廻 糸師凛 黒名蘭世 瀬戸 恵 豊 乙姫 関村".translate(VARIANTS), "蜂樂迴 糸師凜 黑名蘭世 瀨戶 惠 豐 乙姬 關村")

    def test_li_of_a_name(self):
        # (review 2026-11-13: OpenCC wrote 五河琴里 as 五河琴裡; 裡 "inside" is never a name's 里, as for Wikidata)
        rows = character_rows([["五河|琴里"]], set(), {c: f"r{c}" for c in "五河琴里"}, lambda t: t.replace("里", "裡"))
        self.assertEqual(rows[0][1:], ("五河琴里", "五河|琴里"))

    def test_converted_to_taiwan(self):
        rows = character_rows([["灶門|炭治郎"]], set(), self.BEST, lambda t: t.replace("灶", "竈"))
        self.assertEqual(rows[0][1:], ("竈門炭治郎", "竈門|炭治郎"))


if __name__ == "__main__":
    unittest.main()
