from echoclip.captions import build_ass
from echoclip.config import DEFAULTS
from echoclip.edit import find_quote, plan
from echoclip.ingest import _json3_words, words_to_lines
from echoclip.select import _overlap, pick
from echoclip.signals import Signals

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
    ass = build_ass(r["words"], r["duration"], "echo", "Most people quit too early", watermark="@me")
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


def test_new_caption_styles_and_aspects():
    from echoclip.captions import PRESETS
    from echoclip.config import ASPECTS
    ws = words()
    r = plan(ws, ws[0]["s"], ws[-1]["e"], DEFAULTS)
    for style in ("solo", "neon"):
        ass = build_ass(r["words"], r["duration"], style, "Hook", size=ASPECTS["4:5"])
        assert "PlayResY: 1350" in ass and f"Style: Cap,{PRESETS[style]['font']}" in ass
    assert "\\\\blur4" in build_ass(r["words"], r["duration"], "neon").replace("\\", "\\\\")


def test_instagram_check_flags_problems():
    from echoclip.api import instagram_check
    notes = instagram_check({"hook_title": "", "hashtags": ["#a"] * 7, "ig_caption": "just a caption"},
                            {"words": [{"s": 1.2}], "duration": 120})
    text = " ".join(notes)
    assert "starts late" in text and "Over 90" in text and "hook" in text and "hashtags" in text and "send" in text


def test_connect_merges_without_clobbering(tmp_path, monkeypatch):
    import json
    import echoclip.connect as c
    monkeypatch.setattr(c, "H", tmp_path)
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text('{"mcpServers": {"other": {"command": "x"}}}')
    c.TARGETS["cursor"] = [lambda: tmp_path / ".cursor" / "mcp.json"]
    c.connect("cursor", True)
    data = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert "other" in data["mcpServers"] and data["mcpServers"]["echo"]["args"][-1] == "mcp"
    assert (tmp_path / ".cursor" / "mcp.json.bak").exists()


def test_loud_gaps_are_kept_quiet_gaps_are_cut():
    ws = words()
    loud = {"hop": .05, "rms": [.2] * 2000, "speech": .2}
    quiet = {"hop": .05, "rms": [.2] * 2000, "speech": .2}
    for i in range(len(quiet["rms"])):
        t = i * .05
        if not any(w["s"] - .02 <= t <= w["e"] + .02 for w in ws):
            quiet["rms"][i] = .005
    keep = plan(ws, ws[0]["s"], ws[-1]["e"], DEFAULTS, energy=loud)
    cut = plan(ws, ws[0]["s"], ws[-1]["e"], DEFAULTS, energy=quiet)
    assert keep["duration"] > cut["duration"]                 # crowd noise / action survives
    none = plan(ws, ws[0]["s"], ws[-1]["e"], DEFAULTS, energy=quiet, trim=False)
    assert none["duration"] >= keep["duration"]               # --no-trim keeps everything


def test_splice_fills_gaps_from_captions():
    from echoclip.pipeline import _splice
    precise = [{"w": "a", "s": 0.5, "e": 0.8}, {"w": "b", "s": 6.0, "e": 6.3}]
    rough = [{"w": "idiot", "s": 3.0, "e": 3.3}, {"w": "sandwich", "s": 3.4, "e": 3.8}, {"w": "a", "s": .5, "e": .8}]
    out = _splice(precise, rough, 0, 7)
    assert [w["w"] for w in out] == ["a", "idiot", "sandwich", "b"] and out[1]["src"] == "captions"


def test_reframe_never_letterboxes_landscape_without_detectors(monkeypatch, tmp_path):
    import numpy as np
    import echoclip.reframe as rf
    monkeypatch.setattr(rf, "_detectors", lambda: {})          # e.g. OpenCV without the cascades
    monkeypatch.setattr(rf, "probe", lambda p: {"w": 1920, "h": 1080, "dur": 10})
    monkeypatch.setattr(rf, "shots", lambda p: [(0.0, 5.0), (5.0, 10.0)])
    monkeypatch.setattr(rf, "_frames", lambda p: ([i * .25 + .125 for i in range(40)], np.zeros((40, 360, 640), np.uint8)))
    a = rf.analyze(tmp_path / "x.mp4")
    assert a["version"] == 2 and all(sh["mode"] == "crop" for sh in a["shots"])


def test_crashed_tool_gives_a_readable_error():
    import sys
    import pytest
    from echoclip.util import EchoError, run
    with pytest.raises(EchoError, match="no message"):
        run([sys.executable, "-c", "import sys; sys.exit(3)"])


def test_zoom_only_on_real_jump_cuts():
    ws = words()
    r = plan(ws, ws[0]["s"], ws[-1]["e"], DEFAULTS)
    pieces = r["pieces"]
    for a, b in zip(pieces, pieces[1:]):
        if b["src_s"] - a["src_e"] <= .05:          # continuous footage: framing must not change
            assert a["zoom"] == b["zoom"]


def test_camera_holds_inside_deadzone_and_pans_smoothly():
    from echoclip.reframe import _operator
    still = _operator([0, .25, .5, .75, 1], [.5, .51, .49, .52, .5], .316, dead=.035, max_pan=.25)
    assert len(still) <= 2 and all(abs(x - still[0][1]) < 1e-6 for _, x in still)
    moving = _operator([i * .25 for i in range(12)], [.5] * 2 + [.8] * 10, .316, dead=.035, max_pan=.25)
    steps = [b[1] - a[1] for a, b in zip(moving, moving[1:])]
    assert max(steps) <= .25 * .25 + 1e-6            # never jumps faster than the pan limit
    assert moving[-1][1] > .7


def test_direct_validation_and_timeline_mapping():
    import pytest
    from echoclip.direct import _validate
    from echoclip.render import _to_out, _zoom_pass
    from echoclip.util import EchoError
    d = _validate({"shot": 1, "layout": "fill", "focus": [1.4, -1], "zoom": 9,
                   "moves": [{"type": "punch", "to": 3, "at": 5, "hold": 9}]}, 3)
    assert d["focus"] == [1, 0] and d["zoom"] == 2.2 and d["moves"][0]["to"] == 2.2 and d["moves"][0]["hold"] == 4.0
    with pytest.raises(EchoError):
        _validate({"shot": 7}, 3)
    pieces = [{"src_s": 1.0, "src_e": 3.0, "out_s": 0.0}, {"src_s": 5.0, "src_e": 8.0, "out_s": 2.0}]
    assert _to_out(2.0, pieces) == 1.0 and _to_out(4.0, pieces) == 2.0 and _to_out(6.0, pieces) == 3.0
    zp = _zoom_pass([(1.0, 2.0, "push", 1.0, 1.3, 5.0)], [(0, 5, .4, .35)], 30, 1080, 1920)
    assert "zoompan" in zp and "1.300" in zp and "0.400" in zp


def test_pieces_split_at_shot_changes_without_breaking_audio():
    from echoclip.render import _split_at_shots
    pieces = [{"src_s": 0.0, "src_e": 10.0, "out_s": 0.0, "cut_in": True, "cut_out": True, "zoom": 1.0, "group": "body"}]
    analysis = {"shots": [{"s": 0, "e": 4}, {"s": 4, "e": 7}, {"s": 7, "e": 12}]}
    out = _split_at_shots(pieces, analysis)
    assert [(p["src_s"], p["src_e"]) for p in out] == [(0.0, 4), (4, 7), (7, 10.0)]
    assert [p["out_s"] for p in out] == [0.0, 4.0, 7.0]
    assert out[0]["cut_in"] and not out[0]["cut_out"] and not out[1]["cut_in"] and out[-1]["cut_out"]


def test_doctor_handles_missing_parent_package():
    from echoclip.doctor import _has_module
    assert _has_module("json") and not _has_module("nvidia_does_not_exist.cublas")
