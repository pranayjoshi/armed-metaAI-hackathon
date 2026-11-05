"""Calibration tools (armed/calibrate.py, `armed calibrate ...`) and the shoot profile. No
camera, no display, no arm but the virtual one, no network: every writer runs on a
temporary copy of the config files and the records go to a temporary folder."""

from __future__ import annotations

import shutil

import numpy as np
import pytest
import yaml

from armed import calibrate as cal
from armed import cli
from armed.core import ROOT, deep_merge, load_config
from armed.grasp import load_grasp
from armed.robot.skills import load_poses
from armed.routing import load_routes

FIXTURE_POSES = ROOT / "tests" / "fixtures" / "poses.yaml"


@pytest.fixture(autouse=True)
def records(tmp_path, monkeypatch):
    """Records (table.json, zone_*.json, pose_xy.yaml, frames) never land in the repo."""
    monkeypatch.setattr(cal, "CALIBRATION_DIR", tmp_path / "calibration")
    monkeypatch.setattr(cli, "load_env", lambda *a, **k: None)
    return tmp_path / "calibration"


@pytest.fixture
def files(tmp_path):
    """Temporary copies of configs/armed.yaml and configs/routes.yaml, the config pointing
    at the copied routes file and the fixture poses."""
    routes = tmp_path / "routes.yaml"
    shutil.copy(ROOT / "configs" / "routes.yaml", routes)
    cfg = yaml.safe_load((ROOT / "configs" / "armed.yaml").read_text())
    cfg["rules"]["routes_file"] = str(routes)
    cfg["arm"]["poses_file"] = str(FIXTURE_POSES)
    config = tmp_path / "armed.yaml"
    config.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return config, routes


# ── homography ───────────────────────────────────────────────────────────────
# A 0.6 x 0.4 m table seen at 1000 px per metre with a 100 px offset.
IMAGE = [(100.0, 100.0), (700.0, 100.0), (700.0, 500.0), (100.0, 500.0)]
TABLE = cal.table_corners(0.6, 0.4)


def test_fit_homography_maps_the_corners_and_a_fifth_point():
    H = cal.fit_homography(IMAGE, TABLE)
    assert cal.apply_homography(H, IMAGE) == [tuple(p) for p in TABLE]
    assert cal.apply_homography(H, [(400.0, 300.0)]) == [(0.3, 0.2)]
    assert cal.reprojection_errors(H, IMAGE, TABLE) == [0.0, 0.0, 0.0, 0.0]
    assert cal.check_errors(H, [(400.0, 310.0)], [(0.3, 0.2)], 1000.0) == [10.0]


def test_fit_table_scores_check_marks_and_refuses_over_the_limit():
    fit = cal.fit_table(IMAGE, TABLE, {"slot_1": ((400.0, 310.0), (0.3, 0.2))}, 1000.0)
    assert fit.max_reprojection_px == 0.0 and fit.check_mm == {"slot_1": 10.0}
    assert fit.problems(cal.DEFAULTS) == []
    tight = {**cal.DEFAULTS, "max_check_error_mm": 5.0}
    assert fit.problems(tight) == ["slot_1 lands 10 mm from its table xy (limit 5)"]
    # five points with one off by 20 px: least squares, a real reprojection error
    fit = cal.fit_table([*IMAGE, (420.0, 300.0)], [*TABLE, (0.3, 0.2)])
    assert fit.max_reprojection_px > 3.0
    assert any("reprojection error" in p for p in fit.problems(cal.DEFAULTS))


def test_degenerate_or_short_point_sets_are_refused():
    with pytest.raises(cal.CalibrationError):
        cal.fit_homography(IMAGE[:3], TABLE[:3])
    with pytest.raises(cal.CalibrationError):
        cal.fit_homography([(0, 0), (1, 1), (2, 2), (3, 3)], TABLE)
    with pytest.raises(cal.CalibrationError):
        cal.parse_points("1,2;3")
    with pytest.raises(cal.CalibrationError):
        cal.table_corners(0.0, 1.0)


def test_order_corners_sorts_any_click_order():
    shuffled = [IMAGE[2], IMAGE[0], IMAGE[3], IMAGE[1]]
    assert cal.order_corners(shuffled) == IMAGE
    assert cal.box_centres([(0, 0, 10, 20)]) == [(5.0, 10.0)]


def test_write_homography_edits_one_line_and_keeps_the_rest(files):
    config, _ = files
    before = yaml.safe_load(config.read_text())
    H = cal.fit_homography(IMAGE, TABLE)
    cal.write_homography(config, H)
    after = yaml.safe_load(config.read_text())
    assert np.allclose(np.asarray(after["vision"]["table_homography"]), H)
    assert np.allclose(cal.read_homography(after), H)
    del after["vision"]["table_homography"], before["vision"]["table_homography"]
    assert after == before
    assert "armed calibrate table" in config.read_text()


def test_read_homography_rejects_a_malformed_matrix():
    assert cal.read_homography({"vision": {"table_homography": None}}) is None
    with pytest.raises(cal.CalibrationError):
        cal.read_homography({"vision": {"table_homography": [[1, 2], [3, 4]]}})


# ── zones ────────────────────────────────────────────────────────────────────
def test_zone_box_from_any_two_corners():
    assert cal.box_from_corners((0.3, 0.1), (0.1, 0.2)) == (0.1, 0.1, 0.3, 0.2)
    assert cal.parse_box("0.3, 0.2, 0.1, 0.1") == (0.1, 0.1, 0.3, 0.2)
    with pytest.raises(cal.CalibrationError):
        cal.box_from_corners((0.1, 0.1), (0.1, 0.5))


def test_write_zone_replaces_the_placeholder_and_adds_new_ones(files):
    _, routes = files
    assert "PLACEHOLDER, measure at teach time" in routes.read_text()
    cal.write_zone(routes, "laptop", (0.2, 0.1, 0.3, 0.25))
    cal.write_zone(routes, "lamp", (0.5, 0.0, 0.6, 0.1))
    loaded = load_routes(routes)
    assert loaded.zones["laptop"] == (0.2, 0.1, 0.3, 0.25)
    assert loaded.zones["lamp"] == (0.5, 0.0, 0.6, 0.1)
    text = routes.read_text()
    assert "laptop: [0.2, 0.1, 0.3, 0.25]  # measured" in text
    assert "PLACEHOLDER, measure at teach time" not in text.split("zones:")[1].split("\n")[1]
    assert "high_routes:" in text and loaded.handover is not None  # the rest survived
    # measured only once the record matches the file
    assert cal.zone_measured("laptop", loaded.zones["laptop"]) is False
    cal.save_zone_record("laptop", (0.2, 0.1, 0.3, 0.25), None, None)
    assert cal.zone_measured("laptop", loaded.zones["laptop"]) is True
    assert cal.zone_measured("laptop", (0.2, 0.1, 0.3, 0.26)) is False


# ── routes ───────────────────────────────────────────────────────────────────
def test_route_poses_follow_the_taught_route_and_the_handover():
    lib = load_poses(FIXTURE_POSES)
    assert cal.route_poses(lib, "direct", "slot_1") == ["slot_1.above", "tray_above", "tray_release"]
    assert cal.route_poses(lib, "via_high", "slot_2") == [
        "slot_2.above", "via_high", "tray_above", "tray_release"
    ]
    assert cal.route_poses(lib, "handover", None) == ["home", "handover_above", "handover_release"]
    with pytest.raises(cal.CalibrationError):
        cal.route_poses(lib, "via_sky", "slot_1")
    with pytest.raises(cal.CalibrationError):
        cal.route_poses(lib, "direct", None)


def test_virtual_sampling_uses_slot_xy_and_names_what_it_cannot_place():
    lib = load_poses(FIXTURE_POSES)
    names = cal.route_poses(lib, "via_high", "slot_1")
    poly, missing = cal.sample_route_virtual(lib, names, {})
    assert poly == [(0.25, 0.55)] and missing == ["via_high", "tray_above", "tray_release"]
    known = {"via_high": (0.1, 0.4), "tray_above": (0.5, 0.2), "tray_release": (0.5, 0.2)}
    poly, missing = cal.sample_route_virtual(lib, names, known)
    assert missing == [] and cal.dedupe(poly) == [(0.25, 0.55), (0.1, 0.4), (0.5, 0.2)]


def test_pose_xy_record_round_trips(records):
    assert cal.load_pose_xy() == {}
    cal.save_pose_xy({"via_high": (0.1, 0.4)})
    cal.save_pose_xy({"tray_above": (0.5, 0.2)})
    assert cal.load_pose_xy() == {"via_high": (0.1, 0.4), "tray_above": (0.5, 0.2)}
    assert (records / "pose_xy.yaml").exists()


def test_write_route_marks_it_measured_and_keeps_the_others(files):
    _, routes = files
    poly = [(0.25, 0.55), (0.3, 0.2), (0.5, 0.2)]
    cal.write_route(routes, "direct", "slot_1", poly)
    loaded = load_routes(routes)
    assert loaded.routes["direct"]["slot_1"] == poly
    assert ("direct", "slot_1") not in loaded.unmeasured
    assert ("direct", "slot_2") in loaded.unmeasured and ("via_high", "slot_1") in loaded.unmeasured
    assert loaded.routes["via_high"]["slot_3"] == [(0.75, 0.55), (0.1, 0.4), (0.1, 0.0), (0.5, 0.0)]
    # a new route and a new slot are added rather than refused
    cal.write_route(routes, "direct", "slot_9", poly)
    cal.write_route(routes, "via_window", "slot_1", poly)
    loaded = load_routes(routes)
    assert loaded.routes["direct"]["slot_9"] == poly and loaded.routes["via_window"]["slot_1"] == poly
    with pytest.raises(cal.CalibrationError):
        cal.write_route(routes, "direct", "slot_1", poly[:1])


def test_write_handover_sets_polyline_measured_and_spot(files):
    _, routes = files
    assert load_routes(routes).handover.measured is False
    cal.write_handover(routes, [(0.5, 0.7), (0.3, 0.8), (0.15, 0.85)], spot=(0.15, 0.85))
    h = load_routes(routes).handover
    assert h.measured is True and h.polyline == [(0.5, 0.7), (0.3, 0.8), (0.15, 0.85)]
    assert h.spot == (0.15, 0.85) and h.camera_sees_past_arm is False  # never written here
    assert h.start_pose == "home" and h.clear_radius == 0.1


def test_crossings_and_film_verdicts(files):
    _, routes = files
    loaded = load_routes(routes)
    assert cal.crossings([(0.25, 0.55), (0.3, 0.2), (0.5, 0.2)], loaded.zones) == {"laptop": True}
    assert cal.crossings([(0.25, 0.55), (0.1, 0.4), (0.1, 0.0), (0.5, 0.0)], loaded.zones) == {"laptop": False}
    verdicts = cal.film_route_verdicts(loaded, "slot_1", "laptop")
    assert verdicts == [("direct", "placeholder", True, True), ("via_high", "placeholder", False, False)]
    assert cal.film_route_verdicts(loaded, "slot_1", "nothing")[0][1] == "missing"


def test_jog_route_moves_the_virtual_arm_pose_by_pose():
    from armed.robot import build_arm
    from armed.robot.arm import ArmState

    arm = build_arm({"backend": "virtual", "poses_file": str(FIXTURE_POSES), "max_joint_speed_deg_s": 100})
    arm.connect()
    try:
        assert arm.arm()[0]
        seen = []

        def sample(name):
            seen.append((name, round(arm.measured()["shoulder_pan"])))
            return (0.1 * len(seen), 0.2)

        out = cal.jog_route(arm, ["slot_3.above", "home"], sample, speed=1.0, timeout_s=30)
        assert out == {"slot_3.above": (0.1, 0.2), "home": (0.2, 0.2)}
        assert seen[0] == ("slot_3.above", 30) and arm.state is ArmState.READY
        with pytest.raises(cal.CalibrationError):
            cal.jog_route(arm, ["home"], lambda n: None)
    finally:
        arm.close()


# ── jaw ──────────────────────────────────────────────────────────────────────
JAW_CONFIG = """grasp:
  jaw_calibration:
    calibrated: false
    points: [[30, 25], [60, 50], [100, 80]]   # placeholder
  margin_mm: 12
"""


def test_jaw_points_are_written_and_checked(tmp_path):
    config = tmp_path / "armed.yaml"
    config.write_text(JAW_CONFIG)
    with pytest.raises(ValueError):
        cal.parse_jaw_points("30:25,60:50,100:80")  # the placeholder
    with pytest.raises(ValueError):
        cal.parse_jaw_points("30:24,60:20,100:80")  # not increasing
    points = cal.parse_jaw_points("30:24,60:51,100:79")
    cal.write_jaw_points(config, points)
    grasp_cfg, jaw = load_grasp(yaml.safe_load(config.read_text()))
    assert jaw.points == ((30.0, 24.0), (60.0, 51.0), (100.0, 79.0)) and jaw.calibrated is False
    assert "calibrated: false" in config.read_text()
    assert cal.jaw_check(jaw.points, 24.0, grasp_cfg.margin_mm) == (36.0, 43.3)
    assert cal.jaw_check([(30, 25), (60, 50), (100, 80)], 24.0, 12.0) == (36.0, 43.2)


def test_prompt_jaw_points_reads_three_readings_at_the_targets():
    answers = iter(["24", "51", "79"])
    assert cal.prompt_jaw_points(lambda _p: next(answers)) == [(30.0, 24.0), (60.0, 51.0), (100.0, 79.0)]
    with pytest.raises(ValueError):
        cal.prompt_jaw_points(lambda _p: "lots")


# ── slot arc ─────────────────────────────────────────────────────────────────
def test_slot_arc_reports_the_circle_and_joint_spread():
    assert cal.circumcircle([(1, 0), (0, 1), (-1, 0)]) == ((0.0, 0.0), 1.0)
    assert cal.circumcircle([(0, 0), (1, 1), (2, 2)]) is None
    lib = load_poses(FIXTURE_POSES)
    arc = cal.slot_arc(lib, None)
    assert arc["slots"] == ["slot_1", "slot_2", "slot_3"] and arc["joint_spread_deg"] == 0.0
    assert arc["circle"] is None  # the fixture slots are on one line
    arc = cal.slot_arc(lib, (0.5, 0.55))
    assert arc["radii"] == [0.25, 0.0, 0.25] and arc["radius_spread"] == 0.25


# ── check and the shoot profile ──────────────────────────────────────────────
def test_check_reports_every_calibration_and_what_the_film_needs(files, monkeypatch):
    config, routes = files
    monkeypatch.setenv("MODEL_API_KEY", "x")
    report = cal.check(load_config(config))
    rows = {r.item: r for r in report.rows}
    assert rows["homography"].state == "placeholder" and rows["homography"].film
    assert rows["zone laptop"].state == "placeholder"
    assert rows["route direct slot_1"].film and not rows["route direct slot_2"].film
    assert rows["handover"].state == "placeholder" and rows["MODEL_API_KEY"].ok
    assert rows["slot arc"].state == "one arc"
    assert {r.item for r in report.film_missing} == {
        "homography", "zone laptop", "route direct slot_1", "route via_high slot_1", "handover"
    }
    table = report.table()
    assert "MISSING" in table and "route via_left slot_2" in table
    # measure everything the film needs: the report says done
    cal.write_homography(config, cal.fit_homography(IMAGE, TABLE))
    cal.write_zone(routes, "laptop", (0.25, 0.12, 0.35, 0.28))
    cal.save_zone_record("laptop", (0.25, 0.12, 0.35, 0.28), None, None)
    cal.write_route(routes, "direct", "slot_1", [(0.25, 0.55), (0.3, 0.2), (0.5, 0.2)])
    cal.write_route(routes, "via_high", "slot_1", [(0.25, 0.55), (0.1, 0.4), (0.5, 0.0)])
    cal.write_handover(routes, [(0.5, 0.7), (0.15, 0.85)])
    report = cal.check(load_config(config))
    assert report.film_ready, [r.item for r in report.film_missing]
    assert "crosses laptop" in {r.item: r for r in report.rows}["route direct slot_1"].detail


def test_profile_overlay_merges_onto_the_base_config():
    assert deep_merge({"a": {"x": 1, "y": 2}, "b": [1]}, {"a": {"y": 3}, "b": [2]}) == {
        "a": {"x": 1, "y": 3}, "b": [2]
    }
    base, shoot = load_config(), load_config(profile="shoot")
    assert base["arm"]["backend"] == "virtual" and base["selection"]["enabled"] is False
    assert shoot["arm"]["backend"] == "lerobot" and shoot["selection"]["enabled"] is True
    assert shoot["emg"]["source"] == "ganglion" and shoot["speech"]["source"] == "mic"
    assert shoot["vision"]["source"] == "camera"
    assert shoot["selection"]["reaction_s"] == base["selection"]["reaction_s"]  # overlay, not replace
    assert shoot["safety"] == base["safety"]
    with pytest.raises(FileNotFoundError):
        load_config(profile="no_such_profile")


def test_run_shoot_refuses_without_a_key_then_with_placeholder_routes(files, monkeypatch, capsys):
    config, routes = files
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    assert cli.main(["--config", str(config), "run", "--profile", "shoot"]) == 2
    out = capsys.readouterr().out
    assert "Refusing to start the shoot profile" in out and "MODEL_API_KEY is not set" in out
    monkeypatch.setenv("MODEL_API_KEY", "x")
    assert cli.main(["--config", str(config), "run", "--profile", "shoot"]) == 2
    out = capsys.readouterr().out
    assert "route direct slot_1 is a placeholder" in out and "armed calibrate route direct slot_1" in out
    assert "x" not in out.split("MODEL_API_KEY")[1][:5]  # the key value is never printed
    # the routes measured: the preflight passes and the e-stop rule is the next refusal
    cal.write_route(routes, "direct", "slot_1", [(0.25, 0.55), (0.3, 0.2), (0.5, 0.2)])
    cal.write_route(routes, "via_high", "slot_1", [(0.25, 0.55), (0.1, 0.4), (0.5, 0.0)])
    assert cli.main(["--config", str(config), "run", "--profile", "shoot"]) == 2
    out = capsys.readouterr().out
    assert "Refusing to drive the real arm without --i-have-the-estop" in out
    assert "warning: homography is placeholder" in out
    with pytest.raises(SystemExit) as exc:
        cli.main(["--config", str(config), "run", "--profile", "nope"])
    assert exc.value.code == 2 and "configs/nope.yaml is missing" in capsys.readouterr().out


def test_cli_calibrate_route_check_zone_and_jaw_on_the_virtual_backend(files, capsys, monkeypatch):
    config, routes = files
    monkeypatch.setenv("MODEL_API_KEY", "x")
    argv = ["--config", str(config), "calibrate"]
    assert cli.main([*argv, "check"]) == 1
    assert "route direct slot_1    placeholder" in capsys.readouterr().out
    # direct from slot_1: the slot xy plus the typed tray
    rc = cli.main([*argv, "route", "direct", "slot_1", "--xy", "tray_above=0.3,0.2", "--xy", "tray_release=0.3,0.2"])
    out = capsys.readouterr().out
    assert rc == 0 and "measured: true" in out and "crosses: laptop" in out
    assert "direct from slot_1 (measured): crosses laptop: as the film needs" in out
    assert "via_high from slot_1 (placeholder)" in out
    # via_high needs the via pose: refused until it is given, then clear of the laptop
    assert cli.main([*argv, "route", "via_high", "slot_1", "--xy", "tray_above=0.5,0.2"]) == 1
    out = capsys.readouterr().out
    assert "no table xy for via_high, tray_release" in out
    rc = cli.main([*argv, "route", "via_high", "slot_1", "--xy", "via_high=0.1,0.4",
                   "--xy", "tray_above=0.1,0.0", "--xy", "tray_release=0.1,0.0"])
    out = capsys.readouterr().out
    assert rc == 0 and "via_high from slot_1 (measured): clear of laptop: as the film needs" in out
    loaded = load_routes(routes)
    assert ("direct", "slot_1") not in loaded.unmeasured and ("via_high", "slot_1") not in loaded.unmeasured
    # the handover takes no slot
    assert cli.main([*argv, "route", "handover", "--xy", "home=0.5,0.7", "--xy", "handover_above=0.2,0.8",
                     "--xy", "handover_release=0.15,0.85"]) == 0
    assert load_routes(routes).handover.measured is True
    # a zone typed in table xy, then the jaw
    assert cli.main([*argv, "zone", "laptop", "--box", "0.26,0.12,0.34,0.28"]) == 0
    out = capsys.readouterr().out
    assert "direct from slot_1 (measured): crosses laptop" in out
    assert load_routes(routes).zones["laptop"] == (0.26, 0.12, 0.34, 0.28)
    assert cli.main([*argv, "jaw", "--points", "30:24,60:51,100:79"]) == 0
    out = capsys.readouterr().out
    assert "24 mm object opens to 36 mm" in out and "= 43.3% gripper" in out
    assert cli.main([*argv, "jaw", "--points", "30:25,60:50,100:80"]) == 1
    assert "placeholder" in capsys.readouterr().out
    # still MISSING: the homography (no camera here), so the check says so
    assert cli.main([*argv, "check"]) == 1
    out = capsys.readouterr().out
    assert "homography" in out and "1 item(s) the film needs: homography" in out
    assert cli.main([*argv, "table", "--size", "0.6", "0.4", "--points", "100,100;700,100;700,500;100,500", "--no-check"]) == 0
    assert "saved vision.table_homography" in capsys.readouterr().out
    assert cli.main([*argv, "check"]) == 0
    assert "calibrations done" in capsys.readouterr().out


def test_cli_table_refuses_over_the_check_limit_unless_the_tapes_define_the_slots(files, capsys):
    config, _ = files
    argv = ["--config", str(config), "calibrate", "table", "--size", "0.6", "0.4",
            "--points", "100,100;700,100;700,500;100,500"]
    # the fixture slots sit at (0.25, 0.55) etc.: a tape clicked 40 mm away is refused
    assert cli.main([*argv, "--check-points", "350,690;600,650;850,650"]) == 1
    out = capsys.readouterr().out
    assert "not saved" in out and "mm from its table xy" in out
    assert load_config(config)["vision"]["table_homography"] is None
    assert cli.main([*argv, "--check-points", "350,650;600,650;850,650"]) == 0
    assert "0 mm off" in capsys.readouterr().out
    assert load_config(config)["vision"]["table_homography"] is not None


def test_cli_table_write_slots_puts_the_tapes_into_the_poses_file(files, tmp_path, capsys):
    config, _ = files
    poses = tmp_path / "poses.yaml"
    shutil.copy(FIXTURE_POSES, poses)
    argv = ["--config", str(config), "calibrate", "table", "--poses", str(poses), "--size", "0.6", "0.4",
            "--points", "100,100;700,100;700,500;100,500", "--check-points", "200,400;400,400;600,400",
            "--write-slots"]
    assert cli.main(argv) == 0
    assert "wrote slot table_xy" in capsys.readouterr().out
    lib = load_poses(poses)
    assert lib.slots["slot_1"].table_xy == (0.1, 0.3) and lib.slots["slot_3"].table_xy == (0.5, 0.3)
    assert lib.slots["slot_1"].grasp.joints["shoulder_pan"] == -30  # the poses are untouched


def test_sam_markers_fall_back_to_clicks_without_a_client(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "armed.sensors.sam", None)  # import fails: no client
    assert cal.sam_boxes(np.zeros((4, 4, 3), np.uint8), "marker") is None


def test_sam_markers_scale_the_client_boxes_to_pixels(monkeypatch):
    from armed.sensors import sam

    real_client = sam.SamClient

    class FakeScan:
        masks = [sam.Mask("tape", (0.1, 0.2, 0.3, 0.4)), sam.Mask("tape", (0.5, 0.5, 0.6, 0.7))]

    class FakeClient:
        def __init__(self, cfg):
            self.cfg = cfg

        available = True

        def segment_phrases(self, image, phrases):
            assert list(phrases) == ["tape"]
            return FakeScan()

    monkeypatch.setattr(sam, "SamClient", FakeClient)
    boxes = cal.sam_boxes(np.zeros((100, 200, 3), np.uint8), "tape", {"sam_model": "x"})
    assert boxes == [(20.0, 20.0, 60.0, 40.0), (100.0, 50.0, 120.0, 70.0)]
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr(sam, "SamClient", real_client)  # the real one: no key -> None
    assert cal.sam_boxes(np.zeros((100, 200, 3), np.uint8), "tape") is None


def test_writers_cope_with_block_style_files(tmp_path):
    """A file written by a YAML dump (block lists) is edited to one flow line with no
    dangling items: what the jaw writer hit on a dumped config."""
    config = tmp_path / "armed.yaml"
    config.write_text(yaml.safe_dump(yaml.safe_load(JAW_CONFIG)) + "server:\n  port: 1\n")
    cal.write_jaw_points(config, [(30.0, 24.0), (60.0, 51.0), (100.0, 79.0)])
    data = yaml.safe_load(config.read_text())
    assert data["grasp"]["jaw_calibration"]["points"] == [[30, 24], [60, 51], [100, 79]]
    assert data["grasp"]["margin_mm"] == 12 and data["server"] == {"port": 1}
    routes = tmp_path / "routes.yaml"
    routes.write_text(yaml.safe_dump(yaml.safe_load((ROOT / "configs" / "routes.yaml").read_text())))
    cal.write_zone(routes, "laptop", (0.2, 0.1, 0.3, 0.25))
    cal.write_route(routes, "via_high", "slot_2", [(0.5, 0.55), (0.1, 0.4), (0.5, 0.0)])
    cal.write_handover(routes, [(0.5, 0.7), (0.15, 0.85)], spot=(0.15, 0.85))
    loaded = load_routes(routes)
    assert loaded.zones["laptop"] == (0.2, 0.1, 0.3, 0.25)
    assert loaded.routes["via_high"]["slot_2"] == [(0.5, 0.55), (0.1, 0.4), (0.5, 0.0)]
    assert ("via_high", "slot_2") not in loaded.unmeasured and ("via_high", "slot_1") in loaded.unmeasured
    assert loaded.routes["direct"]["slot_3"] == [(0.75, 0.55), (0.3, 0.2), (0.5, 0.2)]
    assert loaded.handover.measured and loaded.handover.spot == (0.15, 0.85)
    assert loaded.handover.clear_radius == 0.1 and loaded.tags["cup"] == ["liquid", "fragile"]
    vision = tmp_path / "v.yaml"
    vision.write_text("vision:\n  table_homography:\n  - [1, 0, 0]\n  - [0, 1, 0]\n  - [0, 0, 1]\n  width: 2\nemg:\n  source: mic\n")
    cal.write_homography(vision, cal.fit_homography(IMAGE, TABLE))
    data = yaml.safe_load(vision.read_text())
    assert np.asarray(data["vision"]["table_homography"]).shape == (3, 3) and data["vision"]["width"] == 2
    assert data["emg"] == {"source": "mic"}
