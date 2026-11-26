// Tests the lookup window's Japanese (#36 plan 7, installer/src/Lookup.cs): the readings of a selection and their romaji,
// and adding a 日文自訂詞彙 - compiled by tools\test_settings.ps1 against Lookup.cs built into a temporary folder with
// data\ja_dict.bin and data\ja_romaji.tsv beside it; a temporary data folder for the words (never %APPDATA%).
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;

static class LookupTest {
    static int failed;

    static void Check(string name, bool ok, string detail) {
        Console.WriteLine((ok ? "ok    " : "FAIL  ") + name + (ok ? "" : "   (" + detail + ")"));
        if (!ok) failed++;
    }

    static int Main() {
        Console.OutputEncoding = Encoding.UTF8;
        var kyou = Lookup.JaReadings("今日", true);
        Check("今日 reads きょう among its readings", kyou.Contains("きょう"), string.Join(",", kyou));
        Check("at most 3 readings, cheapest first", kyou.Count >= 1 && kyou.Count <= 3, string.Join(",", kyou));
        var arigatou = Lookup.JaReadings("ありがとう", true);
        Check("kana reads as itself", arigatou.SequenceEqual(new[] { "ありがとう" }), string.Join(",", arigatou));
        var kata = Lookup.JaReadings("カタカナ", true);
        Check("katakana reads in hiragana", kata.SequenceEqual(new[] { "かたかな" }), string.Join(",", kata));
        Check("日文輸入 off: no Japanese reading", Lookup.JaReadings("今日", false).Count == 0, "");
        // (findings #209: a selection with kana that is no one word of the dictionary - its words' readings pieced
        // together, longest first, kana as it is, 。、？！ kept)
        var sentence = Lookup.JaReadings("日本語を話します", true);
        Check("a sentence with kana reads word by word", sentence.Count == 1 && sentence[0] == "にほんごをはなします",
              string.Join(",", sentence));
        var thanks = Lookup.JaReadings("ありがとう。", true);
        Check("kana with a full stop reads with it", thanks.SequenceEqual(new[] { "ありがとう。" }), string.Join(",", thanks));
        Check("its romaji types the stop too", Lookup.Romaji("ありがとう。") == "arigatou.", Lookup.Romaji("ありがとう。"));
        // (findings #212: 加入自訂詞彙（日文） from a selection with marks - the word its letters, the reading its kana)
        var add = Lookup.JaWordToAdd("ありがとう。", "ありがとう。");
        Check("a word to add leaves the full stop out of both", add.Item1 == "ありがとう" && add.Item2 == "ありがとう",
              add.Item1 + " / " + add.Item2);
        add = Lookup.JaWordToAdd("「ラーメン」", "らーめん");
        Check("...and the brackets out of the word", add.Item1 == "ラーメン" && add.Item2 == "らーめん", add.Item1 + " / " + add.Item2);
        // (findings #213: a kanji outside the BMP is two UTF-16 halves, 〇 is a number letter - both stay in the word)
        add = Lookup.JaWordToAdd("\uD842\uDF9Fる", "しかる");
        Check("a kanji of two UTF-16 halves stays in the word", add.Item1 == "\uD842\uDF9Fる" && add.Item2 == "しかる",
              add.Item1 + " / " + add.Item2);
        add = Lookup.JaWordToAdd("〇〇さん", "まるまるさん");
        Check("〇 stays in the word", add.Item1 == "〇〇さん", add.Item1);
        Check("a kanji of two halves no word holds: none, not left out of the reading",
              Lookup.JaReadings("\uD840\uDC0Bです", true).Count == 0, string.Join(",", Lookup.JaReadings("\uD840\uDC0Bです", true)));
        // (findings #214: an emoji is two halves too but no kanji - left out of the reading as before; a zero-width
        // space copied from a page is no letter of the word)
        var emoji = Lookup.JaReadings("ありがとう\uD83D\uDE0A", true);
        Check("an emoji is left out of the reading", emoji.SequenceEqual(new[] { "ありがとう" }), string.Join(",", emoji));
        add = Lookup.JaWordToAdd("ラーメン\u200B", "らーめん");
        Check("a zero-width space is left out of the word", add.Item1 == "ラーメン", add.Item1.Length.ToString());
        // (findings #215: an emoji is left out of the word as of the reading - the same rule for both)
        add = Lookup.JaWordToAdd("ラーメン\uD83C\uDF5C", "らーめん");
        Check("an emoji is left out of the word", add.Item1 == "ラーメン", add.Item1);
        // (findings #216: a word with a mark after it; #218: the words' readings by the cheapest whole sentence - word
        // costs and the connection costs between them, as the engine's conversion - not each word alone; #217: digits
        // read as a number are beyond it, so no pieced reading)
        var fine = Lookup.JaReadings("大丈夫？", true);
        Check("a word with a question mark reads with it", fine.Count >= 1 && fine[0] == "だいじょうぶ？", string.Join(",", fine));
        // (見に行こう reads ゆこう: 行こ + う costs less than 行こう in Mozc's dictionary, as in the engine's own conversion -
        // the data's call, not the method's)
        foreach (var pair in new[] { Tuple.Create("会議は何時からですか", "かいぎはなんじからですか"),
                                     Tuple.Create("日本に行ったことがある", "にほんにいったことがある"),
                                     Tuple.Create("今日は雨です", "きょうはあめです") }) {
            var got = Lookup.JaReadings(pair.Item1, true);
            Check(pair.Item1 + " reads " + pair.Item2 + " by the whole sentence", got.Count == 1 && got[0] == pair.Item2,
                  string.Join(",", got));
        }
        Check("digits: no pieced reading", Lookup.JaReadings("3時に会いましょう", true).Count == 0 &&
              Lookup.JaReadings("５月に行きます", true).Count == 0, string.Join(",", Lookup.JaReadings("3時に会いましょう", true)));
        // (findings #219: text with kana is Japanese - read with the Japanese voice - whether a reading came or not: digits,
        // a long passage)
        Check("text with kana is Japanese though no reading came", Lookup.IsJapanese("3時に会いましょう", true) &&
              Lookup.JaReadings("3時に会いましょう", true).Count == 0, "");
        Check("kanji alone is not taken for Japanese", !Lookup.IsJapanese("今日", true), "");
        Check("日文輸入 off: nothing is Japanese", !Lookup.IsJapanese("ありがとう", false), "");
        // (findings #220: Chinese with a lone の - no Japanese reading comes - keeps its Chinese row; Japanese with a
        // reading does not get one; kanji alone keeps it as ever)
        foreach (string chinese in new[] { "好吃の店", "我の電腦" })
            Check(chinese + " keeps the Chinese row", Lookup.ChineseRow(chinese, true), string.Join(",", Lookup.JaReadings(chinese, true)));
        Check("a lone の is not Japanese: no pieced reading for 好吃の店", !Lookup.IsJapanese("好吃の店", true) &&
              Lookup.JaReadings("好吃の店", true).Count == 0, string.Join(",", Lookup.JaReadings("好吃の店", true)));
        Check("日本語を話します has no Chinese row", !Lookup.ChineseRow("日本語を話します", true), "");
        Check("今日 keeps its Chinese row", Lookup.ChineseRow("今日", true), "");
        Check("ありがとう has no Chinese row (no 注音)", !Lookup.ChineseRow("ありがとう", true), "");
        Check("a kanji no word holds: none", Lookup.JaReadings("\u3400です", true).Count == 0,
              string.Join(",", Lookup.JaReadings("\u3400です", true)));
        Check("Chinese text with no Japanese word: none", Lookup.JaReadings("我們", true).Count == 0,
              string.Join(",", Lookup.JaReadings("我們", true)));
        Check("romaji of きょう", Lookup.Romaji("きょう") == "kyou", Lookup.Romaji("きょう"));
        Check("romaji of がっこう", Lookup.Romaji("がっこう") == "gakkou", Lookup.Romaji("がっこう"));
        Check("romaji of らーめん", Lookup.Romaji("らーめん") == "ra-menn", Lookup.Romaji("らーめん"));

        string dir = Path.Combine(Path.GetTempPath(), "dualityime-lookup-words");
        if (Directory.Exists(dir)) Directory.Delete(dir, true);
        Directory.CreateDirectory(dir);
        string added = Lookup.AddJaWord("今日", "きょう", dir);
        string again = Lookup.AddJaWord("京", "きょう", dir);
        string file = File.ReadAllText(Path.Combine(dir, "ja_dictionary.json"), Encoding.UTF8);
        Check("加入自訂詞彙（日文） writes ja_dictionary.json", added.Contains("今日") && file.Contains("今日") && file.Contains("\"format\":1"),
              added + " " + file);
        Check("a reading already there gets the word after its own (one reading, several words)",
              again.StartsWith("已加入") && file.IndexOf("今日") >= 0 && file.IndexOf("京") > file.IndexOf("今日"), again + " " + file);
        string twice = Lookup.AddJaWord("京", "きょう", dir);
        file = File.ReadAllText(Path.Combine(dir, "ja_dictionary.json"), Encoding.UTF8);
        Check("the same word and reading again is not added", twice.Contains("已經") &&
              file.IndexOf("京") == file.LastIndexOf("京"), twice + " " + file);
        Check("a reading not hiragana is refused", Lookup.AddJaWord("今日", "kyou", dir).Contains("平假名"), "");
        Directory.Delete(dir, true);
        Console.WriteLine(failed == 0 ? "all passed" : failed + " failed");
        return failed;
    }
}
