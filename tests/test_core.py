from hookcut.captions import build_ass
from hookcut.config import DEFAULTS
from hookcut.edit import find_quote, plan
from hookcut.ingest import _json3_words, words_to_lines
from hookcut.select import _overlap, pick
from hookcut.signals import Signals

TEXT = ("nobody tells you this um but most people quit one week before it works "
        "then one video did a million views overnight and everything changed").split()


def words(gap_at=(4, 12)):
    out, t = [], 1.0
    for i, w in enumerate(TEXT):
        out.append({"w": w, "s": round(t, 2), "e": round(t + 0.3, 2)})
        t += 0.36 + (1.0 if i in gap_at else 0)
    return out


def test_plan_cuts_silence_fillers_and_cold_opens():
    ws = words()
    r = plan(ws, ws[0]["s"], ws[-1]["e"], DEFAULTS, cold_open="one video did a million views overnight",
             keywords=["million"])
    assert r["cold_open"] and r["pieces"][0]["group"] == "cold"
    said = [w["w"] for w in r["words"]]
    assert "um" not in said
    assert said[:3] == ["one", "video", "did"]
    assert any(w["kw"] for w in r["words"])
    raw = ws[-1]["e"] - ws[0]["s"]
    assert r["duration"] < raw  # dead air removed
    starts = [w["s"] for w in r["words"]]
    assert starts == sorted(starts)


def test_find_quote_is_fuzzy():
    ws = words()
    assert find_quote(ws, "one video did a millions views overnight") is not None
    assert find_quote(ws, "completely unrelated sentence here") is None


def test_ass_has_events_and_hook():
    ws = words()
    r = plan(ws, ws[0]["s"], ws[-1]["e"], DEFAULTS)
    ass = build_ass(r["words"], r["duration"], "hookcut", "Most people quit too early", watermark="@me")
    assert "Style: Cap,Anton" in ass and "MOST PEOPLE" in ass and "@me" in ass
    assert ass.count("Dialogue: 1,") >= len(r["words"]) - 2


def test_json3_parse_and_lines():
    data = {"events": [{"tStartMs": 1000, "dDurationMs": 2000, "segs": [
        {"utf8": "hello"}, {"utf8": " world", "tOffsetMs": 500}, {"utf8": " again.", "tOffsetMs": 1200}]}]}
    ws = _json3_words(data)
    assert [w["w"] for w in ws] == ["hello", "world", "again."]
    assert ws[1]["s"] == 1.5
    assert words_to_lines(ws)[0]["text"] == "hello world again."


def test_signals_heat_and_crowd():
    info = {"duration": 300, "heatmap": [{"start_time": i * 3, "end_time": i * 3 + 3,
                                          "value": 1.0 if 40 <= i < 50 else 0.2} for i in range(100)]}
    sig = Signals(info, [{"text": "2:05 had me dying", "likes": 120}, {"text": "2:10!!", "likes": 3},
                         {"text": "at 2:07 lol", "likes": 9}], [])
    assert sig.heat_score(120, 150) > 80 > sig.heat_score(200, 230)
    assert sig.has_crowd and sig.crowd_raw(120, 150) > 0 == sig.crowd_raw(200, 230)


def test_pick_avoids_overlap_and_duplicates():
    a = {"start": 0, "end": 30, "score": 90, "hook_title": "Quit too early", "why": "x"}
    b = {"start": 5, "end": 35, "score": 80, "hook_title": "Different", "why": "y"}
    c = {"start": 100, "end": 130, "score": 70, "hook_title": "Another idea", "why": "z"}
    assert _overlap(a, b) > 0.5
    assert [x["start"] for x in pick([a, b, c], 3)] == [0, 100]
