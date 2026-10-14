"""Yüzen amblem testleri — state_color saf fonksiyon + import-güvenli controller."""
import arda.overlay as overlay


def test_state_color_known_states_distinct():
    assert overlay.state_color("idle") != overlay.state_color("listening")
    assert overlay.state_color("listening") == (0.9, 0.2, 0.2)
    assert overlay.state_color("transcribing") == (0.2, 0.5, 0.9)
    assert overlay.state_color("pasting") == (0.65, 0.4, 0.9)


def test_state_color_unknown_falls_back_to_idle():
    assert overlay.state_color("bilinmeyen") == overlay.state_color("idle")


def test_controller_construct_is_import_safe():
    # pyobjc kurulu olmasa bile yapı çökmemeli (lazy); set_state panel yokken no-op.
    ctrl = overlay.OverlayController(on_toggle=lambda: None)
    ctrl.set_state("idle")
