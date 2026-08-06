from PIL import Image, ImageDraw

from stackdown_word_finder import (
    Tile,
    detect_fully_visible_tiles,
    find_candidate_words,
    guess_prefix,
    identify_guess_row,
    looks_like_capital_b,
    looks_like_capital_i,
    looks_like_capital_n,
    looks_like_capital_o,
    looks_like_capital_p,
    looks_like_capital_z,
    _same_physical_tile,
)

import numpy as np


def make_tile(x, y, letter):
    corners = np.array([[x, y], [x + 40, y], [x + 40, y + 40], [x, y + 40]], dtype=np.float32)
    return Tile(corners, (x + 20, y + 20), 40, 40, 1600, letter, 90)


def test_guess_row_is_lowest_horizontal_run():
    stack = [make_tile(20, 20, "S"), make_tile(70, 40, "T")]
    guess = [make_tile(index * 50, 200, letter) for index, letter in enumerate("CA???")]
    found_guess, visible = identify_guess_row(stack + guess)
    assert [tile.letter for tile in found_guess] == list("CA???")
    assert {tile.letter for tile in visible} == {"S", "T"}
    assert guess_prefix(found_guess) == "CA"


def test_candidates_only_constrain_the_immediate_next_letter():
    words = ["SLEEP", "SPARE", "STARE", "SLAPS", "CRANE"]
    assert set(find_candidate_words("S", "LZPR", words)) == {"SLEEP", "SPARE", "SLAPS"}


def test_complete_guess_needs_no_available_tile():
    assert find_candidate_words("CRANE", "", ["CRANE", "CRATE"]) == ["CRANE"]


def test_single_entered_guess_tile_is_separated_from_stack():
    stack = [
        make_tile(20, 20, "L"),
        make_tile(70, 75, "Z"),
        make_tile(20, 130, "P"),
        make_tile(70, 130, "R"),
    ]
    entered_guess = make_tile(20, 330, "S")
    guess, visible = identify_guess_row(stack + [entered_guess])
    assert guess_prefix(guess) == "S"
    assert {tile.letter for tile in visible} == {"L", "Z", "P", "R"}


def test_normal_stack_row_spacing_is_not_mistaken_for_guess():
    tiles = [make_tile(20, 20, "L"), make_tile(20, 75, "Z"), make_tile(20, 130, "P")]
    guess, visible = identify_guess_row(tiles)
    assert guess == []
    assert len(visible) == 3


def test_lower_guess_zone_handles_tall_detected_tile_borders():
    stack_tile = make_tile(150, 400, "G")
    guess_tiles = [make_tile(index * 50, 520, letter) for index, letter in enumerate("GOIN")]
    guess, visible = identify_guess_row(
        [stack_tile, *guess_tiles], capture_height=600
    )
    assert guess_prefix(guess) == "GOIN"
    assert [tile.letter for tile in visible] == ["G"]


def test_inner_and_outer_contours_are_the_same_physical_tile():
    outer = make_tile(100, 100, "S")
    inner_corners = np.array(
        [[104, 104], [136, 104], [136, 136], [104, 136]], dtype=np.float32
    )
    inner = Tile(inner_corners, (120, 120), 32, 32, 1024, "S", 90)
    assert _same_physical_tile(outer, inner)
    assert not _same_physical_tile(outer, make_tile(150, 100, "Z"))


def test_i_and_o_shape_fallbacks():
    i_image = Image.new("L", (240, 240), 255)
    draw_i = ImageDraw.Draw(i_image)
    draw_i.rounded_rectangle((108, 65, 132, 180), radius=10, fill=0)

    o_image = Image.new("L", (240, 240), 255)
    draw_o = ImageDraw.Draw(o_image)
    draw_o.ellipse((62, 48, 178, 190), fill=0)
    draw_o.ellipse((88, 76, 152, 162), fill=255)

    c_image = Image.new("L", (240, 240), 255)
    draw_c = ImageDraw.Draw(c_image)
    draw_c.arc((62, 48, 178, 190), start=45, end=315, fill=0, width=24)

    assert looks_like_capital_i(i_image)
    assert not looks_like_capital_o(i_image)
    assert looks_like_capital_o(o_image)
    assert not looks_like_capital_o(c_image)


def test_n_shape_fallback_rejects_h():
    n_image = Image.new("L", (240, 240), 255)
    draw_n = ImageDraw.Draw(n_image)
    draw_n.line((68, 185, 68, 52), fill=0, width=22)
    draw_n.line((170, 185, 170, 52), fill=0, width=22)
    draw_n.line((68, 52, 170, 185), fill=0, width=22)

    h_image = Image.new("L", (240, 240), 255)
    draw_h = ImageDraw.Draw(h_image)
    draw_h.line((68, 185, 68, 52), fill=0, width=22)
    draw_h.line((170, 185, 170, 52), fill=0, width=22)
    draw_h.line((68, 120, 170, 120), fill=0, width=22)

    assert looks_like_capital_n(n_image)
    assert not looks_like_capital_n(h_image)


def test_b_shape_fallback_requires_two_bowls():
    b_image = Image.new("L", (240, 240), 255)
    draw_b = ImageDraw.Draw(b_image)
    draw_b.line((68, 190, 68, 48), fill=0, width=24)
    draw_b.rounded_rectangle((62, 48, 174, 122), radius=34, outline=0, width=22)
    draw_b.rounded_rectangle((62, 115, 174, 190), radius=34, outline=0, width=22)

    p_image = Image.new("L", (240, 240), 255)
    draw_p = ImageDraw.Draw(p_image)
    draw_p.line((68, 190, 68, 48), fill=0, width=24)
    draw_p.rounded_rectangle((62, 48, 174, 122), radius=34, outline=0, width=22)

    assert looks_like_capital_b(b_image)
    assert not looks_like_capital_b(p_image)


def test_tile_detection_uses_color_edges_and_rounded_borders():
    # These red and green colors have nearly equal grayscale luminance, so the
    # RGB channel edge maps—not grayscale contrast—must find the four tiles.
    image = Image.new("RGB", (420, 180), (0, 92, 0))
    draw = ImageDraw.Draw(image)
    for index in range(4):
        left = 15 + index * 100
        draw.rounded_rectangle(
            (left, 45, left + 75, 120),
            radius=9,
            fill=(180, 0, 0),
            outline=(230, 20, 20),
            width=3,
        )
    assert len(detect_fully_visible_tiles(image)) == 4


def test_bright_exposed_tile_faces_are_an_independent_signal():
    image = Image.new("RGB", (330, 250), (55, 115, 90))
    draw = ImageDraw.Draw(image)
    # A buried beige layer sits behind a 2x2 group of distinctly lighter
    # playable faces, matching StackDown's exposed-tile visual treatment.
    draw.rounded_rectangle((25, 25, 305, 220), radius=10, fill=(193, 183, 149))
    draw.rounded_rectangle((8, 165, 78, 235), radius=7, fill=(193, 183, 149))
    for row in range(2):
        for column in range(2):
            left, top = 85 + column * 76, 55 + row * 76
            draw.rounded_rectangle(
                (left, top, left + 70, top + 70),
                radius=7,
                fill=(244, 235, 212),
                outline=(255, 250, 235),
                width=2,
            )
            # Repeated square-ish glyph contours must not become the dominant
            # detected size family in place of the actual tile faces.
            draw.rectangle((left + 27, top + 22, left + 42, top + 48), fill=(35, 32, 25))
    tiles = detect_fully_visible_tiles(image)
    centers = {(round(tile.center[0]), round(tile.center[1])) for tile in tiles}
    assert {(120, 90), (196, 90), (120, 166), (196, 166)} <= centers
    assert not any(center[0] < 75 and center[1] > 175 for center in centers)
    assert all(tile.width >= 24 and tile.height >= 24 for tile in tiles)


def test_z_shape_fallback_does_not_confuse_a():
    z_image = Image.new("L", (240, 240), 255)
    z = ImageDraw.Draw(z_image)
    z.line((65, 55, 175, 55), fill=0, width=24)
    z.line((170, 60, 70, 180), fill=0, width=24)
    z.line((65, 185, 175, 185), fill=0, width=24)

    a_image = Image.new("L", (240, 240), 255)
    a = ImageDraw.Draw(a_image)
    a.line((70, 185, 120, 50), fill=0, width=22)
    a.line((120, 50, 175, 185), fill=0, width=22)
    a.line((88, 135, 155, 135), fill=0, width=20)

    p_image = Image.new("L", (240, 240), 255)
    p = ImageDraw.Draw(p_image)
    p.line((75, 190, 75, 50), fill=0, width=23)
    p.line((75, 60, 150, 60), fill=0, width=23)
    p.arc((105, 50, 180, 135), start=-90, end=90, fill=0, width=23)
    p.line((75, 125, 145, 125), fill=0, width=20)

    assert looks_like_capital_z(z_image)
    assert not looks_like_capital_z(a_image)
    assert not looks_like_capital_z(p_image)
    assert looks_like_capital_p(p_image)
